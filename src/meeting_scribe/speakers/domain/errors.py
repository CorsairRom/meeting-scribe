"""Speakers domain errors with stable ``error_code`` values."""

from meeting_scribe.base.domain.errors import InvalidValueError, MeetingScribeError


class DiarizationError(MeetingScribeError):
    error_code = "DIARIZATION_FAILED"


class DiarizationModelAccessError(MeetingScribeError):
    """The pipeline is gated or the Hugging Face login is missing; the fix is a manual step."""

    error_code = "DIARIZATION_MODEL_ACCESS_DENIED"


class SpeakerNameTakenError(InvalidValueError):
    """The name equals another active speaker's display name or default label."""

    error_code = "SPEAKER_NAME_TAKEN"


class SpeakerMergeInvalidError(InvalidValueError):
    """Self merge, a merged-away source or target, or "Me" as the source."""

    error_code = "SPEAKER_MERGE_INVALID"


class TranscriptCorruptError(MeetingScribeError):
    """``utterances.json`` exists but is damaged; the message never quotes its content.

    ``meetings`` maps it to ``MEETING_CORRUPT`` at the boundary (domains do not import each other).
    """

    error_code = "TRANSCRIPT_CORRUPT"


class TranscriptStorageError(MeetingScribeError):
    """``transcript.md`` could not be written; ``meetings`` maps it to a storage failure."""

    error_code = "TRANSCRIPT_STORAGE_FAILED"
