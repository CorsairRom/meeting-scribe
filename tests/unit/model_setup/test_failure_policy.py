"""``FailurePolicy``: which code a failed download reports."""

import pytest

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadFailedError,
    ModelsDownloadReportedError,
    ModelsDownloadTimeoutError,
    ModelsTokenKeyringLockedError,
)
from meeting_scribe.model_setup.domain.models import CacheStatus, FailurePolicy


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            ModelsDownloadReportedError("DIARIZATION_MODEL_ACCESS_DENIED", "x"),
            "DIARIZATION_MODEL_ACCESS_DENIED",
        ),
        (ModelsDownloadReportedError("MODEL_UNAVAILABLE", "x"), "MODEL_UNAVAILABLE"),
        (ModelsDownloadReportedError("GPU_UNAVAILABLE", "x"), "MODELS_DOWNLOAD_FAILED"),
        (ModelsDownloadReportedError("ANYTHING_ELSE", "x"), "MODELS_DOWNLOAD_FAILED"),
        (ModelsDownloadFailedError("x"), "MODELS_DOWNLOAD_FAILED"),
        (ModelsDownloadTimeoutError("x"), "MODELS_DOWNLOAD_TIMEOUT"),
        (ModelsTokenKeyringLockedError("x"), "MODELS_TOKEN_KEYRING_LOCKED"),
        (MeetingScribeError("x"), "MODELS_DOWNLOAD_FAILED"),
    ],
)
def test_the_code_a_failed_download_reports(error: MeetingScribeError, code: str) -> None:
    assert FailurePolicy().code_for(error) == code


def test_a_reported_error_has_its_own_class_code_and_carries_the_process_code() -> None:
    error = ModelsDownloadReportedError("MODEL_UNAVAILABLE", "x")

    assert ModelsDownloadReportedError.error_code == "MODELS_DOWNLOAD_REPORTED"
    assert error.error_code == "MODEL_UNAVAILABLE"


def test_ready_needs_both_models() -> None:
    assert CacheStatus(True, True).ready
    assert not CacheStatus(True, False).ready
    assert not CacheStatus(False, True).ready
