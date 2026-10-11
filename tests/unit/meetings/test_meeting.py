from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from returns.result import Failure

from meeting_scribe.base.domain.errors import InvalidStateTransitionError
from meeting_scribe.meetings.domain.errors import MeetingProcessingActiveError
from meeting_scribe.meetings.domain.meeting import (
    NULL_MEETING,
    AudioInfo,
    Meeting,
    ProcessingDevice,
    ProcessingFailure,
    ProcessingParams,
    RecordingInfo,
    SpeakerEntry,
    SpeakerReview,
    SummaryInfo,
)

T0 = datetime(2026, 10, 9, 10, 15, tzinfo=timezone(timedelta(hours=2)))
RECORDING = RecordingInfo(started_at=T0, stopped_at=T0 + timedelta(minutes=55), state="STOPPED")
FAILURE = ProcessingFailure(stage="DIARIZING", error_code="GPU_OUT_OF_MEMORY", message="oom")
PARAMS = ProcessingParams(transcription={"model": "tiny", "language": "es"})


def _queued() -> Meeting:
    return Meeting.enqueue("m1", Path("/meetings/m1"), RECORDING, T0)


def _at(state: str) -> Meeting:
    meeting = _queued()
    start = lambda m: m.start(T0, PARAMS)  # noqa: E731
    steps = {
        "TRANSCRIBING": [start],
        "DIARIZING": [start, lambda m: m.transcription_done()],
        "EXTRACTING_SAMPLES": [
            start,
            lambda m: m.transcription_done(),
            lambda m: m.diarization_done(),
        ],
    }
    for step in steps.get(state, []):
        meeting = step(meeting).unwrap()
    return meeting


def test_enqueue_creates_a_queued_meeting_with_schema_v1() -> None:
    meeting = _queued()

    assert (meeting.schema_version, meeting.id, meeting.directory) == (
        1,
        "m1",
        Path("/meetings/m1"),
    )
    assert meeting.processing.state == "QUEUED"
    assert meeting.processing.queued_at == T0
    assert meeting.processing.attempt == 1
    assert meeting.recording == RECORDING


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Meeting.enqueue("m1", Path("/m"), RECORDING, datetime(2026, 10, 9, 10, 15))  # noqa: DTZ001


def test_processing_transitions_follow_the_state_machine() -> None:
    later = T0 + timedelta(minutes=5)
    speakers = (SpeakerEntry(id="S1", label="Speaker 1"),)
    queued = _queued()

    started = queued.start(T0 + timedelta(minutes=1), PARAMS).unwrap()
    diarizing = started.transcription_done().unwrap()
    extracting = diarizing.diarization_done().unwrap()
    done = extracting.complete(later, speakers, {"transcript": "transcript.md"}, "es").unwrap()

    assert [m.processing.state for m in (queued, started, diarizing, extracting, done)] == [
        "QUEUED",
        "TRANSCRIBING",
        "DIARIZING",
        "EXTRACTING_SAMPLES",
        "DONE",
    ]
    assert queued.processing.state == "QUEUED"  # entities are immutable
    assert started.processing.started_at == T0 + timedelta(minutes=1)
    assert started.processing.params == PARAMS
    assert done.processing.finished_at == later
    assert (done.speakers, done.files, done.processing.detected_language) == (
        speakers,
        {"transcript": "transcript.md"},
        "es",
    )


@pytest.mark.parametrize(
    ("state", "transition"),
    [
        ("QUEUED", lambda m: m.transcription_done()),
        ("QUEUED", lambda m: m.diarization_done()),
        ("QUEUED", lambda m: m.complete(T0, (), {}, "")),
        ("TRANSCRIBING", lambda m: m.start(T0, PARAMS)),
        ("TRANSCRIBING", lambda m: m.diarization_done()),
        ("DIARIZING", lambda m: m.transcription_done()),
        ("DIARIZING", lambda m: m.complete(T0, (), {}, "")),
        ("EXTRACTING_SAMPLES", lambda m: m.start(T0, PARAMS)),
        ("EXTRACTING_SAMPLES", lambda m: m.transcription_done()),
        ("EXTRACTING_SAMPLES", lambda m: m.diarization_done()),
    ],
)
def test_out_of_order_transitions_are_refused(state: str, transition: Callable) -> None:  # type: ignore[type-arg]
    assert isinstance(transition(_at(state)), Failure)


def test_skipping_a_stage_is_an_invalid_state_transition() -> None:
    result = _queued().transcription_done()

    assert isinstance(result.failure(), InvalidStateTransitionError)
    assert "QUEUED" in str(result.failure())


@pytest.mark.parametrize("state", ["QUEUED", "TRANSCRIBING", "DIARIZING", "EXTRACTING_SAMPLES"])
def test_fail_is_allowed_from_every_non_terminal_state(state: str) -> None:
    failed = _at(state).fail(FAILURE, T0 + timedelta(minutes=9)).unwrap()

    assert failed.processing.state == "FAILED"
    assert failed.processing.failure == FAILURE
    assert failed.processing.finished_at == T0 + timedelta(minutes=9)


def test_fail_from_a_terminal_state_is_refused() -> None:
    failed = _queued().fail(FAILURE, T0).unwrap()

    assert isinstance(failed.fail(FAILURE, T0), Failure)


def test_reprocess_requeues_a_failed_meeting_and_counts_the_attempt() -> None:
    failed = _at("DIARIZING").fail(FAILURE, T0).unwrap()
    later = T0 + timedelta(days=1)

    requeued = failed.reprocess(later).unwrap()

    assert requeued.processing.state == "QUEUED"
    assert requeued.processing.attempt == 2
    assert requeued.processing.queued_at == later
    assert (requeued.processing.failure, requeued.processing.finished_at) == (None, None)
    assert requeued.processing.started_at is None


def test_reprocess_requeues_a_done_meeting() -> None:
    done = _at("DIARIZING").diarization_done().unwrap().complete(T0, (), {}, "es").unwrap()

    assert done.reprocess(T0).unwrap().processing.state == "QUEUED"


@pytest.mark.parametrize("state", ["QUEUED", "TRANSCRIBING", "DIARIZING", "EXTRACTING_SAMPLES"])
def test_reprocess_is_refused_while_processing_is_active(state: str) -> None:
    result = _at(state).reprocess(T0)

    assert isinstance(result.failure(), MeetingProcessingActiveError)


def test_unsupported_meetings_accept_no_transition() -> None:
    unsupported = Meeting.unsupported("m9", Path("/meetings/m9"))

    assert unsupported.processing.state == "UNSUPPORTED"
    assert isinstance(unsupported.reprocess(T0), Failure)
    assert isinstance(unsupported.fail(FAILURE, T0), Failure)


def test_null_meeting_is_null_and_refuses_transitions() -> None:
    assert NULL_MEETING.is_null
    assert not _queued().is_null
    assert isinstance(NULL_MEETING.reprocess(T0), Failure)
    assert isinstance(NULL_MEETING.start(T0, PARAMS), Failure)


def test_with_audio_and_with_device_set_processing_fields_and_keep_state() -> None:
    audio = AudioInfo(chunk_count=2, total_frames=960_960, duration_seconds=60.06)
    device = ProcessingDevice(transcription="cuda/float16")
    base = _at("DIARIZING")

    updated = base.with_audio(audio).with_device(device)

    assert (updated.processing.audio, updated.processing.device) == (audio, device)
    assert updated.processing.state == "DIARIZING"
    assert base.processing.audio is None  # immutable


def test_resume_restarts_an_interrupted_job_keeping_its_queue_position() -> None:
    later = T0 + timedelta(hours=1)
    interrupted = _at("EXTRACTING_SAMPLES")

    resumed = interrupted.resume(later, PARAMS).unwrap()

    assert resumed.processing.state == "TRANSCRIBING"
    assert resumed.processing.queued_at == T0
    assert resumed.processing.started_at == later
    assert resumed.processing.attempt == interrupted.processing.attempt


def test_resume_is_refused_outside_in_progress_states() -> None:
    assert isinstance(_queued().resume(T0, PARAMS), Failure)
    assert isinstance(_queued().fail(FAILURE, T0).unwrap().resume(T0, PARAMS), Failure)


def _reviewed_done() -> Meeting:
    speakers = (
        SpeakerEntry(id="S1", label="Speaker 1", name="Fake One"),
        SpeakerEntry(id="S2", label="Speaker 2"),
        SpeakerEntry(id="S3", label="Speaker 3"),
    )
    done = _at("EXTRACTING_SAMPLES").complete(T0, speakers, {}, "es").unwrap()
    review = SpeakerReview(merged_into={"S3": "S1"}, name_adopted_from={"S1": "S3"})
    return done.with_speaker_review(speakers, review)


def test_a_new_meeting_has_an_empty_speaker_review() -> None:
    review = _queued().speaker_review

    assert (review.merged_into, review.name_adopted_from, review.transcript_stale) == (
        {},
        {},
        False,
    )


def test_a_meeting_json_without_the_section_loads_with_defaults() -> None:
    document = _queued().model_dump(mode="json")
    document.pop("speaker_review")

    loaded = Meeting.model_validate({**document, "directory": Path("/meetings/m1")})

    assert loaded.speaker_review == SpeakerReview()
    assert loaded.schema_version == 1


def test_with_speaker_review_returns_a_copy_with_speakers_and_review() -> None:
    meeting = _reviewed_done()
    renamed = (meeting.speakers[0].model_copy(update={"name": "Fake Two"}), *meeting.speakers[1:])

    changed = meeting.with_speaker_review(renamed, meeting.speaker_review.stale())

    assert changed.speakers[0].name == "Fake Two"
    assert changed.speaker_review.transcript_stale is True
    assert meeting.speakers[0].name == "Fake One"
    assert meeting.speaker_review.transcript_stale is False


def test_the_stale_flag_can_be_cleared_without_touching_the_rest() -> None:
    review = SpeakerReview(merged_into={"S3": "S1"}).stale()

    fresh = review.fresh()

    assert (review.transcript_stale, fresh.transcript_stale) == (True, False)
    assert fresh.merged_into == {"S3": "S1"}


def test_active_speaker_count_leaves_out_merged_speakers() -> None:
    assert _reviewed_done().active_speaker_count == 2
    assert _queued().active_speaker_count == 0


def test_reprocess_clears_the_speaker_review() -> None:
    stale = _reviewed_done()
    stale = stale.with_speaker_review(stale.speakers, stale.speaker_review.stale())

    queued = stale.reprocess(T0).unwrap()

    assert queued.speaker_review == SpeakerReview()


def test_complete_replaces_names_and_merges_with_the_new_run() -> None:
    reviewed = _reviewed_done()
    again = reviewed.reprocess(T0).unwrap()
    for step in (
        lambda m: m.start(T0, PARAMS),
        lambda m: m.transcription_done(),
        lambda m: m.diarization_done(),
    ):
        again = step(again).unwrap()
    fresh = (SpeakerEntry(id="S1", label="Speaker 1"), SpeakerEntry(id="S2", label="Speaker 2"))

    done = again.complete(T0, fresh, {}, "es").unwrap()

    assert [s.name for s in done.speakers] == ["", ""]
    assert done.speaker_review == SpeakerReview()
    assert done.active_speaker_count == 2


def test_complete_clears_a_review_left_by_a_previous_run() -> None:
    meeting = _at("EXTRACTING_SAMPLES").with_speaker_review(
        (), SpeakerReview(merged_into={"S2": "S1"})
    )

    done = meeting.complete(T0, (), {}, "es").unwrap()

    assert done.speaker_review == SpeakerReview()


def _done() -> Meeting:
    return (
        _at("EXTRACTING_SAMPLES")
        .complete(T0, (SpeakerEntry(id="S1", label="Speaker 1"),), {"transcript": "t.md"}, "es")
        .unwrap()
    )


def _summary_info(fingerprint: str = "sha256:aa") -> SummaryInfo:
    return SummaryInfo(
        file="summary.md",
        generated_at=T0,
        provider="local",
        cloud=False,
        model="fake-model",
        language="es",
        title="Synthetic title",
        input_fingerprint=fingerprint,
        chunks=1,
        prompt_version=2,
    )


def test_a_meeting_has_no_summary_section_until_the_first_success() -> None:
    meeting = _done()

    assert meeting.summary is None
    assert "summary" not in meeting.model_dump(mode="json")


def test_with_summary_sets_the_section_and_the_file_entry_on_a_copy() -> None:
    meeting = _done()

    summarized = meeting.with_summary(_summary_info())

    assert meeting.summary is None
    assert summarized.summary == _summary_info()
    assert summarized.files == {"transcript": "t.md", "summary": "summary.md"}
    assert summarized.model_dump(mode="json")["summary"]["input_fingerprint"] == "sha256:aa"


def test_a_new_success_clears_the_last_failure() -> None:
    failed = _done().with_summary(_summary_info()).with_summary_failure("SUMMARY_TIMEOUT", T0)

    assert failed.summary is not None
    assert failed.summary.last_failure is not None
    assert failed.summary.file == "summary.md"  # the previous summary is kept

    again = failed.with_summary(_summary_info("sha256:bb"))

    assert again.summary is not None
    assert again.summary.last_failure is None


def test_a_failure_before_any_success_records_only_the_failure() -> None:
    failed = _done().with_summary_failure("SUMMARY_PROVIDER_UNAVAILABLE", T0)

    assert failed.summary is not None
    assert failed.summary.file == ""
    assert failed.summary.last_failure is not None
    assert failed.summary.last_failure.error_code == "SUMMARY_PROVIDER_UNAVAILABLE"
    assert failed.files == {"transcript": "t.md"}


def test_reprocess_keeps_the_summary_section() -> None:
    summarized = _done().with_summary(_summary_info())

    queued = summarized.reprocess(T0).unwrap()

    assert queued.summary == _summary_info()
    assert queued.files["summary"] == "summary.md"


def test_complete_after_a_reprocess_keeps_the_summary_section() -> None:
    queued = _done().with_summary(_summary_info()).reprocess(T0).unwrap()
    done = (
        queued.start(T0, PARAMS)
        .unwrap()
        .transcription_done()
        .unwrap()
        .diarization_done()
        .unwrap()
        .complete(T0, (), {"transcript": "t.md"}, "es")
        .unwrap()
    )

    assert done.summary == _summary_info()
    assert done.files == {"transcript": "t.md", "summary": "summary.md"}
