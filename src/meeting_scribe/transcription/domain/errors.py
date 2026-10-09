"""Transcription domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import MeetingScribeError


class TranscriptionError(MeetingScribeError):
    error_code = "TRANSCRIPTION_FAILED"
