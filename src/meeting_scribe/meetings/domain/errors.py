"""Meetings domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError


class MeetingStorageError(MeetingScribeError):
    error_code = "MEETING_STORAGE_FAILED"


class MeetingCorruptError(MeetingStorageError):
    """``meeting.json`` exists but is not valid; permanent, so retrying the read cannot help."""

    error_code = "MEETING_CORRUPT"


class MeetingSchemaUnsupportedError(MeetingScribeError):
    error_code = "MEETING_SCHEMA_UNSUPPORTED"


class MeetingProcessingActiveError(InvalidStateTransitionError):
    error_code = "MEETING_PROCESSING_ACTIVE"


class MeetingAudioMissingError(MeetingScribeError):
    error_code = "MEETING_AUDIO_MISSING"


class MeetingAudioUnsupportedError(MeetingScribeError):
    error_code = "MEETING_AUDIO_UNSUPPORTED"


class WorkerCrashedError(MeetingScribeError):
    error_code = "WORKER_CRASHED"


class WorkerTimeoutError(MeetingScribeError):
    error_code = "WORKER_TIMEOUT"


class WorkerProtocolError(MeetingScribeError):
    error_code = "WORKER_PROTOCOL_ERROR"


class WorkerReportedError(MeetingScribeError):
    """A worker's own ``error`` event: the instance carries the worker's ``error_code``."""

    error_code = "WORKER_REPORTED_ERROR"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class WorkerCancelledError(MeetingScribeError):
    """The worker was stopped on request (preemption or shutdown); the job is not failed."""

    error_code = "WORKER_CANCELLED"
