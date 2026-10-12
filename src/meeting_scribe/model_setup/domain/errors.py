"""Model setup errors with stable codes (registered in ``error-codes-catalog.md``)."""

from meeting_scribe.base.domain.errors import MeetingScribeError


class ModelsDownloadActiveError(MeetingScribeError):
    """``Download`` while one is already running."""

    error_code = "MODELS_DOWNLOAD_ACTIVE"


class ModelsDownloadFailedError(MeetingScribeError):
    """The download process failed for a reason with no code of its own."""

    error_code = "MODELS_DOWNLOAD_FAILED"


class ModelsDownloadReportedError(ModelsDownloadFailedError):
    """The download process reported its own ``error_code``; the instance carries it.

    Internal: ``FailurePolicy`` keeps the process's code when the user can act on it and replaces
    it with ``MODELS_DOWNLOAD_FAILED`` otherwise, so this class code never reaches a client.
    """

    error_code = "MODELS_DOWNLOAD_REPORTED"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class ModelsDownloadTimeoutError(MeetingScribeError):
    """The download exceeded ``MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS``."""

    error_code = "MODELS_DOWNLOAD_TIMEOUT"


class ModelsDownloadCancelledError(MeetingScribeError):
    """Cancelled by the user; reported as the ``CANCELLED`` state, never as an error."""

    error_code = "MODELS_DOWNLOAD_CANCELLED"


class ModelsTokenKeyringLockedError(MeetingScribeError):
    """The keyring is locked, so the token cannot be read; the service never prompts."""

    error_code = "MODELS_TOKEN_KEYRING_LOCKED"
