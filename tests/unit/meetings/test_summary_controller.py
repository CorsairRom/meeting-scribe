import logging
from datetime import timedelta

import pytest
from returns.result import Failure

from meeting_scribe.meetings.application.gpu_lease import InMemoryGpuLease
from meeting_scribe.meetings.application.review_controller import ReviewController
from meeting_scribe.meetings.application.summary_controller import (
    ConfiguredProvider,
    SummaryConfig,
    SummaryController,
)
from meeting_scribe.meetings.domain.meeting import (
    Meeting,
    ProcessingFailure,
    ProcessingParams,
    SpeakerEntry,
    SpeakerReview,
)
from meeting_scribe.speakers.application.render_transcript import RenderTranscript, Utterance
from meeting_scribe.speakers.application.review_speakers import ReviewSpeakers
from meeting_scribe.summaries.application.generate_summary import GenerateSummary
from meeting_scribe.summaries.domain.errors import (
    SummaryCloudKeyLockedError,
    SummaryCloudKeyMissingError,
    SummaryProviderUnavailableError,
)
from tests.unit.meetings.fakes import (
    RECORDING,
    ROOT,
    T0,
    FakeClock,
    FakeKeyStore,
    FakeMeetingLock,
    FakeSummaryFiles,
    FakeUtterancesStore,
    InMemoryMeetingRepository,
    ManualStarter,
    RecordingSummaryEvents,
)
from tests.unit.summaries.factories import limits
from tests.unit.summaries.fakes import FakeProvider, FakeReleaser

SPEAKERS = (
    SpeakerEntry(id="ME", label="Me", is_me=True, talk_seconds=5.0, utterance_count=1),
    SpeakerEntry(id="S1", label="Speaker 1", talk_seconds=30.0, utterance_count=3),
    SpeakerEntry(id="S2", label="Speaker 2", talk_seconds=20.0, utterance_count=2),
)
UTTERANCES = (
    Utterance(speaker="S1", start=0.0, end=2.0, text="Synthetic line one."),
    Utterance(speaker="S2", start=2.0, end=4.0, text="Synthetic line two."),
)


class Rig:
    def __init__(
        self, *, cloud_enabled: bool = True, language: str = "auto", threaded: bool = False
    ) -> None:
        self.repository = InMemoryMeetingRepository()
        self.lock = FakeMeetingLock()
        self.repository.lock = self.lock
        self.store = FakeUtterancesStore(self.repository, self.lock)
        self.store.utterances = UTTERANCES
        self.files = FakeSummaryFiles(self.repository, self.lock)
        self.keys = FakeKeyStore()
        self.lease = InMemoryGpuLease()
        self.events = RecordingSummaryEvents()
        self.starter = ManualStarter()
        self.local_provider, self.cloud_provider = FakeProvider(), FakeProvider()
        self.local_releaser = FakeReleaser()
        self.gpu_free_calls = 0
        config = SummaryConfig(
            local=ConfiguredProvider(
                id="local",
                label="Local (fake-model)",
                model="fake-model",
                limits=limits(),
                generate=GenerateSummary(self.local_provider, self.local_releaser),
            ),
            cloud=ConfiguredProvider(
                id="cloud",
                label="cloud: Fake Gateway, fake-cloud-model",
                model="fake-cloud-model",
                limits=limits(),
                generate=GenerateSummary(self.cloud_provider, FakeReleaser()),
            ),
            cloud_enabled=cloud_enabled,
            default_provider="local",
            language=language,
            total_timeout_seconds=600.0,
        )
        review = ReviewController(
            self.repository,
            self.lock,
            ReviewSpeakers(),
            RenderTranscript(self.store),
            samples=None,  # type: ignore[arg-type]  # never used by the summary controller
        )
        self.controller = SummaryController(
            self.repository,
            self.lock,
            review,
            self.files,
            FakeClock(),
            self.lease,
            self.keys,
            config,
            events=self.events,
            start_job=None if threaded else self.starter,
            on_gpu_free=self._gpu_free,
        )

    def _gpu_free(self) -> None:
        self.gpu_free_calls += 1

    def add(
        self,
        meeting_id: str = "m1",
        review: SpeakerReview | None = None,
        speakers: tuple[SpeakerEntry, ...] = SPEAKERS,
    ) -> Meeting:
        meeting = (
            Meeting.enqueue(meeting_id, ROOT / meeting_id, RECORDING, T0)
            .start(T0, _params())
            .unwrap()
            .transcription_done()
            .unwrap()
            .diarization_done()
            .unwrap()
            .complete(T0, speakers, {"transcript": "transcript.md"}, "es")
            .unwrap()
        )
        meeting = meeting.with_speaker_review(speakers, review or SpeakerReview())
        self.repository.meetings[meeting_id] = meeting
        return meeting

    def stored(self, meeting_id: str = "m1") -> Meeting:
        return self.repository.meetings[meeting_id]

    def summarize_and_run(self, meeting_id: str = "m1", provider: str = "local") -> None:
        self.controller.summarize(meeting_id, provider, provider == "cloud").unwrap()
        self.starter.run_next()


def _params() -> ProcessingParams:
    return ProcessingParams()


@pytest.fixture
def rig() -> Rig:
    rig = Rig()
    rig.add()
    return rig


def _code(result) -> str:
    assert isinstance(result, Failure)
    return result.failure().error_code


# -- AC-1: the happy path and its write order --


def test_summarize_saves_the_file_then_the_section(rig: Rig) -> None:
    assert rig.controller.summarize("m1", "local", False).unwrap() == "RUNNING"
    assert rig.files.saves == []  # returned at once, before the model

    rig.starter.run_next()

    stored = rig.stored()
    assert rig.files.saves == ["m1"]
    assert rig.files.section_when_saved == [False]  # the file first, then meeting.json
    assert rig.files.lock_held_on_save == [True]
    assert stored.summary is not None
    assert (stored.summary.file, stored.summary.provider, stored.summary.cloud) == (
        "summary.md",
        "local",
        False,
    )
    assert stored.summary.model == "fake-model"
    assert stored.summary.language == "es"  # the language detected for the meeting
    assert stored.summary.title == "Synthetic title"
    assert stored.summary.input_fingerprint.startswith("sha256:")
    assert stored.summary.last_failure is None
    assert stored.files["summary"] == "summary.md"
    assert "Synthetic title" in rig.files.texts["m1"]
    assert rig.events.finishes() == [("finished", "m1", "DONE", "")]
    assert rig.events.log[-1] == ("active",)
    assert any(entry[0] == "progress" for entry in rig.events.log)


def test_the_model_call_runs_outside_the_meeting_lock(rig: Rig) -> None:
    held: list[bool] = []
    rig.local_provider.on_call = lambda _n: held.append(rig.lock.held)

    rig.summarize_and_run()

    assert held == [False]


def test_the_language_setting_overrides_the_detected_language() -> None:
    rig = Rig(language="fr")
    rig.add()

    rig.summarize_and_run()

    assert rig.stored().summary is not None
    assert rig.stored().summary.language == "fr"  # type: ignore[union-attr]


# -- AC-2, AC-3 --


def _meeting_in(state: str, directory_name: str = "m1") -> Meeting:
    meeting = Meeting.enqueue(directory_name, ROOT / directory_name, RECORDING, T0)
    if state == "UNSUPPORTED":
        return Meeting.unsupported(directory_name, ROOT / directory_name)
    steps = {
        "TRANSCRIBING": lambda m: m.start(T0, _params()),
        "DIARIZING": lambda m: m.start(T0, _params()).bind(lambda x: x.transcription_done()),
        "EXTRACTING_SAMPLES": lambda m: (
            m.start(T0, _params())
            .bind(lambda x: x.transcription_done())
            .bind(lambda x: x.diarization_done())
        ),
        "FAILED": lambda m: m.fail(_failure(), T0),
    }
    return steps[state](meeting).unwrap() if state in steps else meeting


def _failure() -> ProcessingFailure:
    return ProcessingFailure(stage="DIARIZING", error_code="X", message="x")


@pytest.mark.parametrize(
    "state",
    ["QUEUED", "TRANSCRIBING", "DIARIZING", "EXTRACTING_SAMPLES", "FAILED", "UNSUPPORTED"],
)
def test_refused_unless_done(rig: Rig, state: str) -> None:
    rig.repository.meetings["m2"] = _meeting_in(state, "m2")

    result = rig.controller.summarize("m2", "local", False)

    assert _code(result) == "MEETING_NOT_PROCESSED"
    assert rig.starter.jobs == []
    assert rig.files.saves == []
    assert rig.controller.active_meeting_id == ""


def test_an_unknown_meeting_is_entity_not_found(rig: Rig) -> None:
    assert _code(rig.controller.summarize("nope", "local", False)) == "ENTITY_NOT_FOUND_MEETING"


def test_no_speech_is_not_summarized(rig: Rig) -> None:
    rig.store.utterances = ()

    result = rig.controller.summarize("m1", "local", False)

    assert _code(result) == "SUMMARY_NO_SPEECH"
    assert rig.starter.jobs == []
    assert rig.local_provider.requests == []
    assert rig.controller.active_meeting_id == ""


def test_the_provider_gets_the_effective_names_and_merges() -> None:
    rig = Rig()
    named = tuple(s.model_copy(update={"name": "Alex"}) if s.id == "S1" else s for s in SPEAKERS)
    rig.add(review=SpeakerReview(merged_into={"S2": "S1"}), speakers=named)

    rig.summarize_and_run()

    prompt = "\n".join(m.content for m in rig.local_provider.requests[0])
    assert prompt.count("Alex") >= 2  # both lines and the participants
    assert "Speaker 2" not in prompt
    assert "m1" not in prompt.replace("fake-model", "")  # no meeting id (AC-7)


# -- AC-5, AC-6: cloud gates and provider rows --


def test_cloud_gates() -> None:
    off = Rig(cloud_enabled=False)
    off.add()
    assert _code(off.controller.summarize("m1", "cloud", True)) == "SUMMARY_CLOUD_DISABLED"
    assert off.keys.reads == 0
    assert off.starter.jobs == []

    rig = Rig()
    rig.add()
    assert _code(rig.controller.summarize("m1", "cloud", False)) == "SUMMARY_CLOUD_NOT_CONFIRMED"
    assert _code(rig.controller.summarize("m1", "nope", True)) == "INVALID_VALUE"
    assert rig.keys.reads == 0  # the keyring is touched only for a confirmed cloud request

    rig.keys.failure = SummaryCloudKeyMissingError("missing")
    assert _code(rig.controller.summarize("m1", "cloud", True)) == "SUMMARY_CLOUD_KEY_MISSING"
    rig.keys.failure = SummaryCloudKeyLockedError("locked")
    assert _code(rig.controller.summarize("m1", "cloud", True)) == "SUMMARY_CLOUD_KEY_LOCKED"
    assert rig.starter.jobs == []
    assert rig.controller.active_meeting_id == ""  # a refused request holds no slot

    rig.keys.failure = None
    assert rig.controller.summarize("m1", "cloud", True).unwrap() == "RUNNING"


def test_a_local_request_never_reads_the_key(rig: Rig) -> None:
    rig.summarize_and_run()

    assert rig.keys.reads == 0


def test_a_confirmed_cloud_summary_is_labelled_and_takes_no_gpu(rig: Rig) -> None:
    rig.lease.acquire("processing")  # the GPU is busy; the cloud does not care

    rig.summarize_and_run(provider="cloud")

    info = rig.stored().summary
    assert info is not None
    assert (info.provider, info.cloud, info.model) == ("cloud", True, "fake-cloud-model")
    assert rig.local_provider.requests == []
    assert len(rig.cloud_provider.requests) == 1


def test_providers_rows(rig: Rig) -> None:
    local, cloud = rig.controller.providers()

    assert (local.id, local.label, local.cloud, local.available, local.default) == (
        "local",
        "Local (fake-model)",
        False,
        True,
        True,
    )
    assert (cloud.id, cloud.cloud, cloud.available, cloud.reason, cloud.default) == (
        "cloud",
        True,
        True,
        "",
        False,
    )
    assert "cloud" in cloud.label
    assert rig.keys.reads == 0

    off = Rig(cloud_enabled=False)
    _, disabled = off.controller.providers()
    assert (disabled.available, disabled.reason) == (False, "SUMMARY_CLOUD_DISABLED")


# -- AC-11: one at a time --


def test_single_slot(rig: Rig) -> None:
    rig.add("m2")
    rig.controller.summarize("m1", "local", False).unwrap()

    assert rig.controller.active_meeting_id == "m1"
    assert _code(rig.controller.summarize("m1", "local", False)) == "SUMMARY_ALREADY_RUNNING"
    assert _code(rig.controller.summarize("m2", "local", False)) == "SUMMARY_BUSY"
    assert _code(rig.controller.summarize("m2", "cloud", True)) == "SUMMARY_BUSY"

    rig.starter.run_next()

    assert rig.controller.active_meeting_id == ""
    assert rig.controller.summarize("m2", "local", False).unwrap() == "RUNNING"


# -- AC-12: the GPU --


def test_local_summary_respects_the_gpu(rig: Rig) -> None:
    rig.lease.acquire("processing")
    assert _code(rig.controller.summarize("m1", "local", False)) == "SUMMARY_GPU_BUSY"
    rig.lease.release("processing")

    rig.lease.acquire("recording")
    assert _code(rig.controller.summarize("m1", "local", False)) == "SUMMARY_GPU_BUSY"
    assert rig.controller.active_meeting_id == ""
    rig.lease.release("recording")

    rig.controller.summarize("m1", "local", False).unwrap()
    assert not rig.lease.available("processing")  # held for the whole job
    rig.starter.run_next()

    assert rig.lease.available("processing")
    assert rig.gpu_free_calls == 1
    assert rig.local_releaser.calls == 1  # the model was released before the lease was given back


def test_a_recording_start_cancels_a_local_summary(rig: Rig) -> None:
    rig.controller.summarize("m1", "local", False).unwrap()

    rig.lease.acquire("recording")  # what ProcessingController.preempt() does
    rig.starter.run_next()

    assert rig.files.saves == []
    assert rig.stored().summary is None
    assert rig.events.finishes() == [("finished", "m1", "CANCELLED", "SUMMARY_CANCELLED")]
    assert not rig.lease.available("processing")  # the recording still holds the GPU


def test_a_recording_start_does_not_cancel_a_cloud_summary(rig: Rig) -> None:
    rig.controller.summarize("m1", "cloud", True).unwrap()

    rig.lease.acquire("recording")
    rig.starter.run_next()

    assert rig.events.finishes() == [("finished", "m1", "DONE", "")]


# -- AC-10: cancel, shutdown --


def test_cancel_stops_the_job_and_leaves_the_previous_summary(rig: Rig) -> None:
    rig.summarize_and_run()
    before = rig.stored().summary
    text = rig.files.texts["m1"]
    rig.controller.summarize("m1", "local", False).unwrap()

    assert rig.controller.cancel("m1").unwrap() is True
    rig.starter.run_next()

    assert rig.files.texts["m1"] == text
    assert rig.stored().summary == before  # no failure is recorded for a cancel
    assert rig.events.finishes()[-1] == ("finished", "m1", "CANCELLED", "SUMMARY_CANCELLED")
    assert rig.controller.cancel("m1").unwrap() is False  # idle: false, not an error


def test_cancel_of_an_unknown_meeting_is_entity_not_found(rig: Rig) -> None:
    assert _code(rig.controller.cancel("nope")) == "ENTITY_NOT_FOUND_MEETING"


def test_shutdown_aborts() -> None:
    rig = Rig(threaded=True)  # the default starter: a real thread
    rig.add()
    rig.local_provider.block_until_cancelled = True
    rig.controller.summarize("m1", "local", False).unwrap()

    rig.controller.shutdown()

    assert rig.files.saves == []
    assert rig.stored().summary is None
    assert rig.controller.active_meeting_id == ""
    assert rig.lease.available("processing")
    assert rig.events.finishes() == [("finished", "m1", "CANCELLED", "SUMMARY_CANCELLED")]
    assert _code(rig.controller.summarize("m1", "local", False)) == "SUMMARY_BUSY"


# -- AC-13: failures --


def test_failure_is_recorded_and_previous_kept(rig: Rig) -> None:
    rig.summarize_and_run()
    text, before = rig.files.texts["m1"], rig.stored().summary
    assert before is not None
    rig.local_provider.responder = lambda _m: SummaryProviderUnavailableError("down")

    rig.summarize_and_run()

    after = rig.stored().summary
    assert after is not None
    assert after.last_failure is not None
    assert after.last_failure.error_code == "SUMMARY_PROVIDER_UNAVAILABLE"
    assert after.last_failure.at > T0
    assert after.model_copy(update={"last_failure": None}) == before
    assert rig.files.texts["m1"] == text
    assert rig.events.finishes()[-1] == (
        "finished",
        "m1",
        "FAILED",
        "SUMMARY_PROVIDER_UNAVAILABLE",
    )
    status = rig.controller.get_status("m1").unwrap()
    assert (status.state, status.failure_code) == ("FAILED", "SUMMARY_PROVIDER_UNAVAILABLE")
    assert status.generated_at is not None  # the previous summary still exists


def test_a_failure_before_any_summary_is_recorded_without_a_file(rig: Rig) -> None:
    rig.local_provider.responder = lambda _m: SummaryProviderUnavailableError("down")

    rig.summarize_and_run()

    info = rig.stored().summary
    assert info is not None
    assert info.file == ""
    assert "summary" not in rig.stored().files
    status = rig.controller.get_status("m1").unwrap()
    assert (status.state, status.generated_at) == ("FAILED", None)


def test_a_new_success_clears_the_failure(rig: Rig) -> None:
    rig.local_provider.responder = lambda _m: SummaryProviderUnavailableError("down")
    rig.summarize_and_run()
    rig.local_provider.responder = FakeProvider().responder

    rig.summarize_and_run()

    assert rig.controller.get_status("m1").unwrap().state == "DONE"


def test_a_file_write_failure_fails_the_summary_and_keeps_the_old_one(rig: Rig) -> None:
    rig.files.fail_saves = True

    rig.summarize_and_run()

    assert rig.events.finishes()[-1][2:] == ("FAILED", "SUMMARY_STORAGE_FAILED")
    assert rig.stored().summary is not None
    assert rig.stored().summary.file == ""  # type: ignore[union-attr]


def test_a_meeting_json_write_failure_fails_the_summary(rig: Rig) -> None:
    rig.repository.fail_saves_for = {"m1"}

    rig.summarize_and_run()

    assert rig.events.finishes()[-1][2:] == ("FAILED", "MEETING_STORAGE_FAILED")


def test_a_meeting_reprocessed_while_the_summary_runs_discards_the_result(rig: Rig) -> None:
    rig.controller.summarize("m1", "local", False).unwrap()
    rig.repository.meetings["m1"] = rig.stored().reprocess(T0 + timedelta(hours=1)).unwrap()

    rig.starter.run_next()

    assert rig.files.saves == []
    assert rig.events.finishes() == [("finished", "m1", "FAILED", "MEETING_NOT_PROCESSED")]
    assert rig.stored().summary is None  # nothing is written to a meeting that is being processed


# -- AC-15: backup and staleness --


def test_backup_rotation(rig: Rig) -> None:
    rig.summarize_and_run()
    first = rig.files.texts["m1"]

    rig.summarize_and_run()

    assert rig.files.previous["m1"] == first
    assert rig.files.saves == ["m1", "m1"]


def test_stale_is_computed_from_the_fingerprint(rig: Rig) -> None:
    rig.summarize_and_run()
    text = rig.files.texts["m1"]
    assert rig.controller.get_status("m1").unwrap().stale is False
    assert rig.controller.get_summary("m1").unwrap().stale is False

    renamed = tuple(s.model_copy(update={"name": "Alex"}) if s.id == "S1" else s for s in SPEAKERS)
    rig.repository.meetings["m1"] = rig.stored().with_speaker_review(renamed, SpeakerReview())

    assert rig.controller.get_status("m1").unwrap().stale is True
    assert rig.controller.get_summary("m1").unwrap().stale is True
    assert rig.files.texts["m1"] == text  # never modified

    rig.repository.meetings["m1"] = rig.stored().with_speaker_review(SPEAKERS, SpeakerReview())
    assert rig.controller.get_status("m1").unwrap().stale is False  # computed, not stored


def test_a_reprocessed_meeting_shows_its_summary_as_out_of_date(rig: Rig) -> None:
    rig.summarize_and_run()
    text = rig.files.texts["m1"]
    rig.repository.meetings["m1"] = rig.stored().reprocess(T0 + timedelta(hours=1)).unwrap()

    status = rig.controller.get_status("m1").unwrap()

    assert (status.state, status.stale) == ("DONE", True)
    assert rig.files.texts["m1"] == text
    assert rig.controller.get_summary("m1").unwrap().stale is True


# -- reads --


def test_status_and_summary_before_any_summary(rig: Rig) -> None:
    status = rig.controller.get_status("m1").unwrap()

    assert (status.state, status.stale, status.generated_at, status.failure_code) == (
        "NONE",
        False,
        None,
        "",
    )
    assert _code(rig.controller.get_summary("m1")) == "ENTITY_NOT_FOUND_SUMMARY"
    assert _code(rig.controller.get_status("nope")) == "ENTITY_NOT_FOUND_MEETING"


def test_status_while_running_carries_the_progress(rig: Rig) -> None:
    rig.controller.summarize("m1", "cloud", True).unwrap()

    running = rig.controller.get_status("m1").unwrap()

    assert (running.state, running.cloud, running.step, running.total_steps) == (
        "RUNNING",
        True,
        0,
        1,
    )
    assert "Fake Gateway" in running.provider_label
    rig.starter.run_next()


def test_get_summary_returns_the_text_and_facts_without_a_path(rig: Rig) -> None:
    rig.summarize_and_run(provider="cloud")

    document = rig.controller.get_summary("m1").unwrap()

    assert "Synthetic title" in document.markdown
    assert (document.title, document.provider_label, document.cloud) == (
        "Synthetic title",
        "cloud: Fake Gateway, fake-cloud-model",
        True,
    )
    assert (document.model, document.language, document.stale) == ("fake-cloud-model", "es", False)
    assert not hasattr(document, "path")


def test_a_deleted_summary_file_is_state_none(rig: Rig) -> None:
    rig.summarize_and_run()
    del rig.files.texts["m1"]

    status = rig.controller.get_status("m1").unwrap()

    assert (status.state, status.stale, status.generated_at) == ("NONE", False, None)
    assert _code(rig.controller.get_summary("m1")) == "ENTITY_NOT_FOUND_SUMMARY"
    assert rig.controller.summarize("m1", "local", False).unwrap() == "RUNNING"  # works again


# -- AC-7, AC-9: nothing private in the logs --


def test_no_secret_in_logs(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig.summarize_and_run(provider="cloud")
    rig.cloud_provider.responder = lambda _m: SummaryProviderUnavailableError("down")
    rig.summarize_and_run(provider="cloud")

    logged = caplog.text
    assert FakeKeyStore.FAKE_KEY not in logged
    assert "Synthetic line" not in logged
    assert "Alex" not in logged
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("m1" in r.getMessage() and "cloud" in r.getMessage() for r in info)
    assert all(FakeKeyStore.FAKE_KEY not in str(r.__dict__) for r in caplog.records)
