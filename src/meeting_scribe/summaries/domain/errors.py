"""Summaries domain errors with stable ``error_code`` values.

Messages name the next step and never quote transcript text, names, summary text or keys.
"""

from meeting_scribe.base.domain.errors import MeetingScribeError


class SummaryError(MeetingScribeError):
    """Base of every summary failure; the subclasses carry the stable codes."""

    error_code = "SUMMARY_FAILED"


class SummaryNoSpeechError(SummaryError):
    error_code = "SUMMARY_NO_SPEECH"


class SummaryProviderUnavailableError(SummaryError):
    error_code = "SUMMARY_PROVIDER_UNAVAILABLE"


class SummaryModelNotFoundError(SummaryError):
    error_code = "SUMMARY_MODEL_NOT_FOUND"


class SummaryAuthFailedError(SummaryError):
    error_code = "SUMMARY_AUTH_FAILED"


class SummaryRateLimitedError(SummaryError):
    error_code = "SUMMARY_RATE_LIMITED"


class SummaryProviderRejectedError(SummaryError):
    error_code = "SUMMARY_PROVIDER_REJECTED"


class SummaryTimeoutError(SummaryError):
    error_code = "SUMMARY_TIMEOUT"


class SummaryContextTooLargeError(SummaryError):
    error_code = "SUMMARY_CONTEXT_TOO_LARGE"


class SummaryOutputInvalidError(SummaryError):
    error_code = "SUMMARY_OUTPUT_INVALID"


class SummaryCancelledError(SummaryError):
    error_code = "SUMMARY_CANCELLED"
