import pytest

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.summaries.domain import errors

CODES = {
    errors.SummaryNoSpeechError: "SUMMARY_NO_SPEECH",
    errors.SummaryProviderUnavailableError: "SUMMARY_PROVIDER_UNAVAILABLE",
    errors.SummaryModelNotFoundError: "SUMMARY_MODEL_NOT_FOUND",
    errors.SummaryAuthFailedError: "SUMMARY_AUTH_FAILED",
    errors.SummaryRateLimitedError: "SUMMARY_RATE_LIMITED",
    errors.SummaryProviderRejectedError: "SUMMARY_PROVIDER_REJECTED",
    errors.SummaryTimeoutError: "SUMMARY_TIMEOUT",
    errors.SummaryContextTooLargeError: "SUMMARY_CONTEXT_TOO_LARGE",
    errors.SummaryOutputInvalidError: "SUMMARY_OUTPUT_INVALID",
    errors.SummaryCancelledError: "SUMMARY_CANCELLED",
    errors.SummaryCloudKeyMissingError: "SUMMARY_CLOUD_KEY_MISSING",
    errors.SummaryCloudKeyLockedError: "SUMMARY_CLOUD_KEY_LOCKED",
}


@pytest.mark.parametrize(("error_type", "code"), CODES.items())
def test_error_codes_are_stable_and_derive_from_summary_error(
    error_type: type[errors.SummaryError], code: str
) -> None:
    error = error_type("message")

    assert error.error_code == code
    assert isinstance(error, errors.SummaryError)
    assert isinstance(error, MeetingScribeError)
