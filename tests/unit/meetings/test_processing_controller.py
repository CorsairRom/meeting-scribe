import logging
import threading
import time
from datetime import timedelta

import pytest
from pydantic import ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingConfig,
    ProcessingController,
    ProcessingStatus,
)
from meeting_scribe.meetings.domain.errors import (
    MeetingCorruptError,
    MeetingProcessingActiveError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    WorkerCancelledError,
    WorkerCrashedError,
    WorkerReportedError,
)
from meeting_scribe.meetings.domain.meeting import (
    Meeting,
    ProcessingFailure,
    ProcessingParams,
    RecordingInfo,
)
from meeting_scribe.meetings.domain.ports import CancelSignal
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ResultEvent,
    StageEvent,
    WorkerJob,
)
from tests.unit.meetings.fakes import (
    RECORDING,
    ROOT,
    T0,
    FakeAudioInspector,
    FakeClock,
    FakeMonotonic,
    InMemoryMeetingRepository,
    OnEvent,
    RecordingEvents,
    ScriptedRunner,
    diarize_ok,
    speakers,
    transcribe_ok,
)

PARAMS = ProcessingParams(transcription={"model": "tiny"}, speakers={"pipeline": "p"})


def _stage_event_then_result(
    job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
) -> Result[ResultEvent, MeetingScribeError]:
    """A transcribe worker that wrongly announces the diarize hand-off."""
    if isinstance(failed := on_event(StageEvent(state="EXTRACTING_SAMPLES")), Failure):
        return failed
    return Success(ResultEvent())


def _cache_hit_then_result(
    job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
) -> Result[ResultEvent, MeetingScribeError]:
    on_event(CacheHitEvent(artifact="words.json"))
    return Success(ResultEvent())


class Fixture:
    def __init__(self) -> None:
        self.repository = InMemoryMeetingRepository()
        self.runner = ScriptedRunner()
        self.audio = FakeAudioInspector()
        self.events = RecordingEvents(self.repository)
        self.config = ProcessingConfig(
            device="cuda",
            worker_timeout_factor=1.0,
            worker_min_timeout_seconds=900.0,
            params=PARAMS,
            storage_retry_delays_seconds=(2.0, 8.0, 30.0),
        )
        self.monotonic = FakeMonotonic()
        self.controller = ProcessingController(
            repository=self.repository,
            runner=self.runner,
            audio=self.audio,
            clock=FakeClock(),
            meetings_dir=ROOT,
            config=self.config,
            events=self.events,
            monotonic=self.monotonic,
        )

    def seed(self, meeting_id: str, minutes: int, state: str) -> Meeting:
        recording = RecordingInfo(
            started_at=T0 + timedelta(minutes=minutes),
            stopped_at=T0 + timedelta(minutes=minutes + 5),
        )
        queued = Meeting.enqueue(meeting_id, ROOT / meeting_id, recording, recording.started_at)
        meeting = queued
        if state in ("TRANSCRIBING", "DIARIZING", "EXTRACTING_SAMPLES", "DONE"):
            meeting = meeting.start(T0, PARAMS).unwrap()
        if state in ("DIARIZING", "EXTRACTING_SAMPLES", "DONE"):
            meeting = meeting.transcription_done().unwrap()
        if state in ("EXTRACTING_SAMPLES", "DONE"):
            meeting = meeting.diarization_done().unwrap()
        if state == "DONE":
            meeting = meeting.complete(T0, speakers(), {}, "es").unwrap()
        self.repository.save(meeting).unwrap()
        return meeting

    def state(self, meeting_id: str) -> str:
        return self.repository.meetings[meeting_id].processing.state

    def advance(self, seconds: float) -> None:
        self.monotonic.advance(seconds)

    def run_all(self) -> None:
        while self.controller.run_next():
            pass


def test_stopped_recording_is_enqueued() -> None:
    fx = Fixture()

    meeting = fx.controller.enqueue("m1", RECORDING).unwrap()

    stored = fx.repository.meetings["m1"]
    assert (meeting, stored.processing.state, stored.schema_version) == (stored, "QUEUED", 1)
    assert stored.directory == ROOT / "m1"
    assert stored.recording == RECORDING
    assert fx.events.state_changes == [("m1", "QUEUED", "QUEUED")]
    assert fx.controller.queued_ids() == ("m1",)


def test_a_failed_capture_with_chunks_is_enqueued_like_a_clean_stop() -> None:
    fx = Fixture()
    crashed = RecordingInfo(
        started_at=T0, stopped_at=T0 + timedelta(minutes=3), state="FAILED", failure_reason="died"
    )

    fx.controller.enqueue("m1", crashed).unwrap()

    assert fx.state("m1") == "QUEUED"
    assert fx.repository.meetings["m1"].recording.failure_reason == "died"


def test_enqueue_twice_does_not_duplicate_the_job() -> None:
    fx = Fixture()

    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.enqueue("m1", RECORDING).unwrap()

    assert fx.controller.queued_ids() == ("m1",)


def test_enqueue_returns_a_failure_when_the_meeting_cannot_be_saved() -> None:
    fx = Fixture()
    fx.repository.fail_saves = True

    result = fx.controller.enqueue("m1", RECORDING)

    assert isinstance(result, Failure)
    assert fx.controller.queued_ids() == ()


def test_job_runs_through_the_states_and_each_is_persisted_before_its_signal() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()

    assert fx.controller.run_next() is True

    assert [(i, s) for i, s, _ in fx.events.state_changes] == [
        ("m1", "QUEUED"),
        ("m1", "TRANSCRIBING"),
        ("m1", "DIARIZING"),
        ("m1", "EXTRACTING_SAMPLES"),
        ("m1", "DONE"),
    ]
    assert all(signalled == stored for _, signalled, stored in fx.events.state_changes)
    done = fx.repository.meetings["m1"]
    assert done.speakers == speakers()
    assert done.files == {"transcript": "transcript.md"}
    assert done.processing.detected_language == "es"
    assert done.processing.params == PARAMS
    assert done.processing.audio is not None
    assert done.processing.device.transcription == "cuda/float16"
    assert done.processing.device.diarization == "cuda"
    assert fx.events.finished_ids == ["m1"]
    assert fx.runner.stages_for("m1") == ["transcribe", "diarize"]
    assert fx.controller.run_next() is False


def test_worker_gets_the_device_params_and_a_timeout_from_the_audio_length() -> None:
    fx = Fixture()  # 480_640 frames = 30.04 s, so the 900 s floor applies
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.run_all()

    assert {t for _, _, t in fx.runner.calls} == {900.0}
    fx.audio.frames = 16_000 * 3_600
    fx.controller.enqueue("m2", RECORDING).unwrap()
    fx.run_all()
    assert {t for mid, _, t in fx.runner.calls if mid == "m2"} == {3_600.0}


def test_an_out_of_order_stage_event_fails_the_job_with_a_protocol_error() -> None:
    fx = Fixture()
    fx.runner.behaviours[("m1", "transcribe")] = _stage_event_then_result
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert fx.state("m1") == "FAILED"
    assert failure is not None
    assert (failure.error_code, failure.stage) == ("WORKER_PROTOCOL_ERROR", "TRANSCRIBING")
    assert fx.runner.stages_for("m1") == ["transcribe"]
    assert fx.events.finished_ids == ["m1"]


def test_a_result_without_the_stage_event_is_a_protocol_error() -> None:
    fx = Fixture()
    fx.runner.behaviours[("m1", "diarize")] = lambda job, on_event, cancel: Success(ResultEvent())
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert failure is not None
    assert (failure.error_code, failure.stage) == ("WORKER_PROTOCOL_ERROR", "DIARIZING")


def test_worker_errors_are_stored_with_their_code_and_stage() -> None:
    fx = Fixture()
    fx.runner.behaviours[("m1", "diarize")] = lambda job, on_event, cancel: Failure(
        WorkerReportedError("DIARIZATION_MODEL_ACCESS_DENIED", "run hf auth login")
    )
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert failure is not None
    assert failure.error_code == "DIARIZATION_MODEL_ACCESS_DENIED"
    assert failure.message == "run hf auth login"
    assert failure.stage == "DIARIZING"


def test_a_crash_fails_the_job_with_the_runner_message() -> None:
    fx = Fixture()
    fx.runner.behaviours[("m1", "transcribe")] = lambda job, on_event, cancel: Failure(
        WorkerCrashedError("exit code -9; stderr: killed")
    )
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert failure is not None
    assert failure.error_code == "WORKER_CRASHED"
    assert "-9" in failure.message


def test_missing_audio_fails_the_queued_job_without_starting_a_worker() -> None:
    fx = Fixture()
    fx.audio.missing.add("m1")
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert failure is not None
    assert (failure.error_code, failure.stage) == ("MEETING_AUDIO_MISSING", "QUEUED")
    assert fx.runner.calls == []
    assert fx.events.finished_ids == ["m1"]


def test_resume_requeues_unfinished_jobs_only() -> None:
    fx = Fixture()
    fx.seed("B", 5, "DIARIZING")
    fx.seed("C", 10, "DONE")
    fx.seed("A", 0, "QUEUED")
    fx.seed("D", 15, "QUEUED")
    failed = fx.repository.meetings["D"].fail(
        ProcessingFailure(stage="QUEUED", error_code="X", message="m"),
        T0,
    )
    fx.repository.save(failed.unwrap()).unwrap()
    done_before = fx.repository.meetings["C"], fx.repository.meetings["D"]
    fx.runner.behaviours[("B", "transcribe")] = _cache_hit_then_result

    assert fx.controller.resume().unwrap() == 2
    assert fx.controller.queued_ids() == ("A", "B")
    fx.run_all()

    assert (fx.repository.meetings["C"], fx.repository.meetings["D"]) == done_before
    assert (fx.state("A"), fx.state("B")) == ("DONE", "DONE")
    assert next(c for c in fx.runner.calls if c[0] in "AB")[0] == "A"
    assert fx.runner.stages_for("B") == ["transcribe", "diarize"]  # cache hit, then diarization
    assert fx.repository.meetings["B"].processing.attempt == 1


def test_a_meeting_stored_mid_extraction_resumes_and_completes() -> None:
    fx = Fixture()
    fx.seed("E", 0, "EXTRACTING_SAMPLES")

    fx.controller.resume().unwrap()
    fx.run_all()

    assert fx.state("E") == "DONE"
    assert fx.repository.meetings["E"].processing.queued_at == T0


def test_reprocess_requeues_a_done_meeting_and_runs_it_again() -> None:
    fx = Fixture()
    fx.seed("m1", 0, "DONE")

    meeting = fx.controller.reprocess("m1").unwrap()

    assert (meeting.processing.state, meeting.processing.attempt) == ("QUEUED", 2)
    assert fx.controller.queued_ids() == ("m1",)
    fx.run_all()
    assert fx.state("m1") == "DONE"


def test_reprocess_is_refused_while_active_and_for_unknown_meetings() -> None:
    fx = Fixture()
    fx.seed("m1", 0, "QUEUED")

    active = fx.controller.reprocess("m1")
    unknown = fx.controller.reprocess("nope")

    assert isinstance(active.failure(), MeetingProcessingActiveError)
    assert isinstance(unknown.failure(), MeetingScribeError)
    assert unknown.failure().error_code == "ENTITY_NOT_FOUND_MEETING"


def test_a_failing_events_observer_never_changes_the_job() -> None:
    fx = Fixture()

    class Exploding:
        def state_changed(self, meeting: Meeting) -> None:
            raise RuntimeError("boom")

        def finished(self, meeting: Meeting) -> None:
            raise RuntimeError("boom")

        def storage_failed(self, meeting_id: str, error_code: str) -> None:
            raise RuntimeError("boom")

        def status_changed(self) -> None:
            raise RuntimeError("boom")

    fx.controller._events = Exploding()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.run_all()

    assert fx.state("m1") == "DONE"


def _disk_fills_during_transcription(fx: Fixture):
    def behaviour(
        job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
    ) -> Result[ResultEvent, MeetingScribeError]:
        fx.repository.fail_saves = True
        del fx.runner.behaviours[("m1", "transcribe")]  # only the first attempt
        return Success(ResultEvent())

    return behaviour


def test_a_save_failure_mid_job_requeues_the_job_instead_of_stranding_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fx = Fixture()
    fx.runner.behaviours[("m1", "transcribe")] = _disk_fills_during_transcription(fx)
    fx.controller.enqueue("m1", RECORDING).unwrap()

    with caplog.at_level(logging.ERROR):
        fx.controller.run_next()

    assert fx.state("m1") == "TRANSCRIBING"  # disk still says what is true
    assert fx.controller.queued_ids() == ("m1",)
    assert fx.events.finished_ids == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)

    fx.repository.fail_saves = False
    fx.advance(2.0)
    fx.run_all()

    assert fx.state("m1") == "DONE"
    assert fx.events.finished_ids == ["m1"]


def test_a_job_that_cannot_start_because_of_a_save_failure_is_requeued() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves = True

    fx.controller.run_next()

    assert fx.runner.calls == []
    assert fx.controller.queued_ids() == ("m1",)
    fx.repository.fail_saves = False
    fx.advance(2.0)
    fx.run_all()
    assert fx.state("m1") == "DONE"


def _run_until_given_up(fx: Fixture) -> None:
    for delay in (0.0, *fx.config.storage_retry_delays_seconds):  # first run, then one per retry
        fx.advance(delay)
        fx.run_all()


def test_a_job_that_keeps_failing_to_save_is_given_up_but_stays_resumable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves = True

    with caplog.at_level(logging.ERROR):
        _run_until_given_up(fx)

    assert fx.controller.queued_ids() == ()
    assert len([r for r in caplog.records if "giving up" in r.getMessage()]) == 1
    assert len(fx.runner.calls) == 0
    assert fx.state("m1") == "QUEUED"
    fx.repository.fail_saves = False
    assert fx.controller.resume().unwrap() == 1
    fx.run_all()
    assert fx.state("m1") == "DONE"


def test_a_storage_retry_waits_for_its_delay_and_the_delays_grow() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves = True

    assert fx.controller.run_next() is True  # run 1 fails
    fx.repository.fail_saves = False
    assert fx.controller.queued_ids() == ("m1",)  # waiting, still known
    fx.advance(1.9)
    assert fx.controller.run_next() is False  # not due yet
    fx.advance(0.1)
    fx.repository.fail_saves = True
    assert fx.controller.run_next() is True  # run 2 fails
    fx.advance(7.9)
    assert fx.controller.run_next() is False  # second delay is 8 s
    fx.advance(0.1)
    assert fx.controller.run_next() is True  # run 3 fails
    assert len(fx.repository.saves) == 1  # only the enqueue save ever succeeded


def test_a_waiting_retry_never_blocks_other_jobs() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.enqueue("m2", RECORDING).unwrap()
    fx.repository.fail_saves_for.add("m1")

    fx.run_all()  # m1 fails to save and waits; m2 runs meanwhile

    assert fx.state("m2") == "DONE"
    assert fx.controller.queued_ids() == ("m1",)
    fx.repository.fail_saves_for.clear()
    fx.advance(2.0)
    fx.run_all()
    assert fx.state("m1") == "DONE"


def test_giving_up_tells_the_observers_so_the_ui_can_show_it() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves = True

    _run_until_given_up(fx)

    assert fx.events.storage_failures == [("m1", "MEETING_STORAGE_FAILED")]


def test_a_transient_read_failure_is_its_own_outcome_and_backs_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fx = Fixture()
    fx.seed("m1", 0, "QUEUED")
    fx.controller.resume()
    fx.repository.get_failures["m1"] = MeetingStorageError("input/output error")

    with caplog.at_level(logging.WARNING):
        fx.run_all()

    assert "could not be read" in caplog.text
    assert "could not be saved" not in caplog.text
    assert fx.runner.calls == []
    assert fx.controller.queued_ids() == ("m1",)
    del fx.repository.get_failures["m1"]
    fx.run_all()
    assert fx.controller.queued_ids() == ("m1",)  # still waiting
    fx.advance(2.0)
    fx.run_all()
    assert fx.state("m1") == "DONE"


def test_a_read_failure_that_never_clears_is_reported_after_the_budget() -> None:
    fx = Fixture()
    fx.seed("m1", 0, "QUEUED")
    fx.controller.resume()
    fx.repository.get_failures["m1"] = MeetingStorageError("input/output error")

    _run_until_given_up(fx)

    assert fx.controller.queued_ids() == ()
    assert fx.events.storage_failures == [("m1", "MEETING_STORAGE_FAILED")]


@pytest.mark.parametrize(
    "error",
    [
        MeetingSchemaUnsupportedError("schema 2"),
        MeetingCorruptError("corrupt meeting.json"),
    ],
)
def test_a_permanent_read_failure_is_reported_and_skipped_without_retries(
    error: MeetingStorageError | MeetingSchemaUnsupportedError,
) -> None:
    fx = Fixture()
    fx.seed("m1", 0, "QUEUED")
    fx.seed("m2", 1, "QUEUED")
    fx.controller.resume()
    fx.repository.get_failures["m1"] = error

    fx.run_all()

    assert fx.controller.queued_ids() == ()
    assert fx.state("m2") == "DONE"
    assert fx.runner.stages_for("m1") == []
    assert fx.events.storage_failures == [("m1", error.error_code)]
    fx.advance(100.0)
    assert fx.controller.run_next() is False


def test_the_storage_event_never_carries_what_the_repository_said() -> None:
    fx = Fixture()
    fx.seed("m1", 0, "QUEUED")
    fx.controller.resume()
    fx.repository.get_failures["m1"] = MeetingCorruptError("input_value='private words'")

    fx.run_all()

    assert fx.events.storage_failures == [("m1", "MEETING_CORRUPT")]


def test_a_meeting_enqueued_during_a_storage_failing_run_is_queued_once() -> None:
    fx = Fixture()

    def behaviour(
        job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
    ) -> Result[ResultEvent, MeetingScribeError]:
        fx.repository.fail_saves = True
        del fx.runner.behaviours[("m1", "transcribe")]
        fx.controller.enqueue("m1", RECORDING).unwrap()  # the user asks again mid-run
        return Success(ResultEvent())

    fx.runner.behaviours[("m1", "transcribe")] = behaviour
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.run_next()
    fx.repository.fail_saves = False

    assert fx.controller.queued_ids() == ("m1",)
    fx.advance(100.0)
    fx.run_all()
    assert fx.state("m1") == "DONE"
    assert fx.runner.stages_for("m1") == ["transcribe", "transcribe", "diarize"]


def test_checking_readiness_never_reorders_the_queue() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves = True
    fx.controller.run_next()
    fx.advance(2.0)  # the retry is now due

    with fx.controller._condition:
        fx.controller._can_start()

    assert list(fx.controller._queue) == []
    fx.controller._promote_due_retries()
    assert list(fx.controller._queue) == ["m1"]


def test_serve_forever_wakes_up_for_a_waiting_retry() -> None:
    fx = Fixture()
    fx.config = fx.config.model_copy(update={"storage_retry_delays_seconds": (0.3, 0.3)})
    fx.controller._config = fx.config
    fx.controller._monotonic = time.monotonic  # the one wiring test uses a real (tiny) delay
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.repository.fail_saves_for.add("m1")
    worker = threading.Thread(target=fx.controller.serve_forever, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while fx.controller._storage_runs.get("m1") != 1 and time.monotonic() < deadline:
        time.sleep(0.01)  # until the first run has failed and a retry waits
    fx.repository.fail_saves_for.clear()
    while fx.state("m1") != "DONE" and time.monotonic() < deadline:
        time.sleep(0.01)
    fx.controller.shutdown()
    worker.join(timeout=5)

    assert fx.state("m1") == "DONE"
    assert not worker.is_alive()


def test_a_save_failure_that_still_lets_the_failure_be_recorded_fails_the_job() -> None:
    fx = Fixture()
    flaky = fx.repository.save
    saves = {"count": 0}

    def save_failing_once(meeting: Meeting):
        saves["count"] += 1
        if saves["count"] == 4:  # enqueue, start, device record, then transcription_done fails
            return Failure(MeetingStorageError("disk full"))
        return flaky(meeting)

    fx.repository.save = save_failing_once  # type: ignore[method-assign]
    fx.controller.enqueue("m1", RECORDING).unwrap()

    fx.run_all()

    failure = fx.repository.meetings["m1"].processing.failure
    assert fx.state("m1") == "FAILED"
    assert failure is not None
    assert failure.error_code == "MEETING_STORAGE_FAILED"
    assert fx.events.finished_ids == ["m1"]


def test_enqueue_of_a_finished_meeting_reprocesses_it_and_ignores_the_recording() -> None:
    fx = Fixture()
    fx.seed("m1", 0, "DONE")
    other = RecordingInfo(started_at=T0, stopped_at=T0 + timedelta(minutes=1))

    meeting = fx.controller.enqueue("m1", other).unwrap()

    assert (meeting.processing.state, meeting.processing.attempt) == ("QUEUED", 2)
    assert meeting.recording != other


@pytest.mark.parametrize(
    ("device", "factor", "floor"),
    [("cpu", 0.0, 60.0), ("cpu", 1.0, 0.0), ("tpu", 1.0, 60.0)],
)
def test_processing_config_validates_its_values(device: str, factor: float, floor: float) -> None:
    with pytest.raises(ValidationError):
        ProcessingConfig(
            device=device,  # type: ignore[arg-type]
            worker_timeout_factor=factor,
            worker_min_timeout_seconds=floor,
            params=PARAMS,
            storage_retry_delays_seconds=(1.0,),
        )


def test_processing_config_has_no_defaults_of_its_own() -> None:
    with pytest.raises(ValidationError) as raised:
        ProcessingConfig()  # type: ignore[call-arg]

    missing = {e["loc"][0] for e in raised.value.errors() if e["type"] == "missing"}
    assert missing == set(ProcessingConfig.model_fields)  # every field, params included


@pytest.mark.parametrize("delays", [(0.0,), (-1.0,), (2.0, -1.0)])
def test_processing_config_rejects_non_positive_retry_delays(delays: tuple[float, ...]) -> None:
    with pytest.raises(ValidationError):
        ProcessingConfig(
            device="cpu",
            worker_timeout_factor=1.0,
            worker_min_timeout_seconds=60.0,
            params=PARAMS,
            storage_retry_delays_seconds=delays,
        )


IDLE = ProcessingStatus(active_meeting_id="", active_state="IDLE", queued_count=0)


def test_status_is_idle_with_nothing_queued() -> None:
    assert Fixture().controller.status() == IDLE


def test_status_counts_waiting_jobs_and_announces_every_change() -> None:
    fx = Fixture()

    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.enqueue("m2", RECORDING).unwrap()

    assert fx.controller.status() == ProcessingStatus(
        active_meeting_id="", active_state="IDLE", queued_count=2
    )
    assert fx.events.status_changes >= 2


def test_status_follows_the_running_job_and_returns_to_idle() -> None:
    fx = Fixture()
    seen: list[ProcessingStatus] = []

    def watch(job: WorkerJob, on_event: OnEvent, cancel: CancelSignal):
        seen.append(fx.controller.status())
        return (
            diarize_ok(job, on_event, cancel)
            if job.stage == "diarize"
            else transcribe_ok(job, on_event, cancel)
        )

    fx.runner.behaviours[("m1", "transcribe")] = watch
    fx.runner.behaviours[("m1", "diarize")] = watch
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.enqueue("m2", RECORDING).unwrap()

    fx.controller.run_next()

    assert seen == [
        ProcessingStatus(active_meeting_id="m1", active_state="TRANSCRIBING", queued_count=1),
        ProcessingStatus(active_meeting_id="m1", active_state="DIARIZING", queued_count=1),
    ]
    assert fx.controller.status() == ProcessingStatus(
        active_meeting_id="", active_state="IDLE", queued_count=1
    )


def test_a_preempted_job_is_idle_and_waiting_again() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()

    def preempted(job: WorkerJob, on_event: OnEvent, cancel: CancelSignal):
        fx.controller.preempt()
        return Failure(WorkerCancelledError("stopped"))

    fx.runner.behaviours[("m1", "transcribe")] = preempted

    fx.controller.run_next()

    assert fx.controller.status() == ProcessingStatus(
        active_meeting_id="", active_state="IDLE", queued_count=1
    )


def test_get_and_list_read_through_the_repository() -> None:
    fx = Fixture()
    fx.seed("old", 0, "DONE")
    fx.seed("new", 30, "QUEUED")

    assert [m.id for m in fx.controller.list_meetings().unwrap()] == ["new", "old"]
    assert fx.controller.get_meeting("old").unwrap().processing.state == "DONE"
    missing = fx.controller.get_meeting("nope").failure()
    assert missing.error_code == "ENTITY_NOT_FOUND_MEETING"


def test_get_passes_a_storage_failure_through() -> None:
    fx = Fixture()
    fx.repository.get_failures["bad"] = MeetingCorruptError("damaged")

    assert fx.controller.get_meeting("bad").failure().error_code == "MEETING_CORRUPT"


def test_an_unexpected_crash_fails_the_job_and_announces_status_and_failure() -> None:
    fx = Fixture()

    def explode(job: WorkerJob, on_event: OnEvent, cancel: CancelSignal):
        raise RuntimeError("the runner is broken")

    fx.runner.behaviours[("m1", "transcribe")] = explode
    fx.controller.enqueue("m1", RECORDING).unwrap()
    announced = fx.events.status_changes

    assert fx.controller.run_next() is True

    failure = fx.repository.meetings["m1"].processing.failure
    assert failure is not None
    assert failure.error_code == "INTERNAL_ERROR"
    assert "broken" not in failure.message  # a fixed message: no exception text is stored
    assert ("m1", "FAILED", "FAILED") in fx.events.state_changes
    assert fx.events.finished_ids == ["m1"]
    assert fx.events.status_changes > announced  # the return to idle is announced too
    assert fx.controller.status().active_state == "IDLE"
