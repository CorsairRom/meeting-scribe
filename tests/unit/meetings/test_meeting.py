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
    Meeting,
    ProcessingFailure,
    ProcessingParams,
    RecordingInfo,
    SpeakerEntry,
)

T0 = datetime(2026, 10, 9, 10, 15, tzinfo=timezone(timedelta(hours=2)))
RECORDING = RecordingInfo(started_at=T0, stopped_at=T0 + timedelta(minutes=55), state="STOPPED")
FAILURE = ProcessingFailure(stage="DIARIZING", error_code="GPU_OUT_OF_MEMORY", message="oom")
PARAMS = ProcessingParams(transcription={"model": "tiny", "language": "es"})


def _queued() -> Meeting:
    return Meeting.enqueue("m1", Path("/meetings/m1"), RECORDING, T0)


def _at(state: str) -> Meeting:
    meeting = _queued()
    steps = {
        "TRANSCRIBING": [lambda m: m.start(T0, PARAMS)],
        "DIARIZING": [lambda m: m.start(T0, PARAMS), lambda m: m.transcription_done()],
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
    ],
)
def test_out_of_order_transitions_are_refused(state: str, transition: Callable) -> None:  # type: ignore[type-arg]
    assert isinstance(transition(_at(state)), Failure)


def test_skipping_a_stage_is_an_invalid_state_transition() -> None:
    result = _queued().transcription_done()

    assert isinstance(result.failure(), InvalidStateTransitionError)
    assert "QUEUED" in str(result.failure())


@pytest.mark.parametrize("state", ["QUEUED", "TRANSCRIBING", "DIARIZING"])
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


@pytest.mark.parametrize("state", ["QUEUED", "TRANSCRIBING", "DIARIZING"])
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
