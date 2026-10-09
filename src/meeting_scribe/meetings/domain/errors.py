"""Meetings domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError


class MeetingStorageError(MeetingScribeError):
    error_code = "MEETING_STORAGE_FAILED"


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
