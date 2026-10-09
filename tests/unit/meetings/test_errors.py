import pytest

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingProcessingActiveError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    WorkerCrashedError,
    WorkerProtocolError,
    WorkerTimeoutError,
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (MeetingStorageError, "MEETING_STORAGE_FAILED"),
        (MeetingSchemaUnsupportedError, "MEETING_SCHEMA_UNSUPPORTED"),
        (MeetingProcessingActiveError, "MEETING_PROCESSING_ACTIVE"),
        (MeetingAudioMissingError, "MEETING_AUDIO_MISSING"),
        (MeetingAudioUnsupportedError, "MEETING_AUDIO_UNSUPPORTED"),
        (WorkerCrashedError, "WORKER_CRASHED"),
        (WorkerTimeoutError, "WORKER_TIMEOUT"),
        (WorkerProtocolError, "WORKER_PROTOCOL_ERROR"),
    ],
)
def test_meetings_error_codes_are_stable(error: type[MeetingScribeError], code: str) -> None:
    assert error.error_code == code
    assert issubclass(error, MeetingScribeError)


def test_processing_active_is_an_invalid_state_transition() -> None:
    assert issubclass(MeetingProcessingActiveError, InvalidStateTransitionError)
