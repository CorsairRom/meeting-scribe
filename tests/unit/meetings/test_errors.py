import pytest

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingCorruptError,
    MeetingNotProcessedError,
    MeetingProcessingActiveError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    SummaryAlreadyRunningError,
    SummaryBusyError,
    SummaryCloudDisabledError,
    SummaryCloudNotConfirmedError,
    SummaryGpuBusyError,
    SummaryStorageError,
    WorkerCrashedError,
    WorkerProtocolError,
    WorkerTimeoutError,
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (MeetingStorageError, "MEETING_STORAGE_FAILED"),
        (MeetingCorruptError, "MEETING_CORRUPT"),
        (MeetingSchemaUnsupportedError, "MEETING_SCHEMA_UNSUPPORTED"),
        (MeetingProcessingActiveError, "MEETING_PROCESSING_ACTIVE"),
        (MeetingNotProcessedError, "MEETING_NOT_PROCESSED"),
        (MeetingAudioMissingError, "MEETING_AUDIO_MISSING"),
        (MeetingAudioUnsupportedError, "MEETING_AUDIO_UNSUPPORTED"),
        (SummaryCloudDisabledError, "SUMMARY_CLOUD_DISABLED"),
        (SummaryCloudNotConfirmedError, "SUMMARY_CLOUD_NOT_CONFIRMED"),
        (SummaryAlreadyRunningError, "SUMMARY_ALREADY_RUNNING"),
        (SummaryBusyError, "SUMMARY_BUSY"),
        (SummaryGpuBusyError, "SUMMARY_GPU_BUSY"),
        (SummaryStorageError, "SUMMARY_STORAGE_FAILED"),
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


def test_not_processed_is_an_invalid_state_transition() -> None:
    assert issubclass(MeetingNotProcessedError, InvalidStateTransitionError)
