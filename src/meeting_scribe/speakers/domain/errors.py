"""Speakers domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import MeetingScribeError


class DiarizationError(MeetingScribeError):
    error_code = "DIARIZATION_FAILED"


class DiarizationModelAccessError(MeetingScribeError):
    """The pipeline is gated or the Hugging Face login is missing; the fix is a manual step."""

    error_code = "DIARIZATION_MODEL_ACCESS_DENIED"
