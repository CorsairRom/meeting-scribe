"""Recording domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError


class RecordingAlreadyActiveError(InvalidStateTransitionError):
    error_code = "RECORDING_ALREADY_ACTIVE"


class NoActiveRecordingError(InvalidStateTransitionError):
    error_code = "NO_ACTIVE_RECORDING"


class AudioDeviceNotFoundError(MeetingScribeError):
    error_code = "AUDIO_DEVICE_NOT_FOUND"


class AudioCaptureError(MeetingScribeError):
    error_code = "AUDIO_CAPTURE_FAILED"


class RecordingStorageError(MeetingScribeError):
    error_code = "RECORDING_STORAGE_FAILED"
