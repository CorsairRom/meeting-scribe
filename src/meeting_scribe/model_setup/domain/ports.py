"""Ports of the model setup domain; the adapters (keyring, child processes) are infrastructure."""

from collections.abc import Callable
from typing import Protocol

from pydantic import SecretStr
from returns.result import Result

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import ModelsTokenKeyringLockedError
from meeting_scribe.model_setup.domain.models import CacheStatus, Outcome

type ProgressCallback = Callable[[str, int, int], None]
"""``(model, step, total_steps)``; ``model`` is ``pipeline`` or ``whisper``."""


class CancelSignal(Protocol):
    """Set from another thread to stop a running download (``threading.Event`` fits)."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class TokenSource(Protocol):
    """The Hugging Face token in the keyring (never a setting, never a D-Bus value)."""

    def token_stored(self) -> bool:
        """Whether the keyring item exists. The secret is not read; never raises, never prompts."""
        ...

    def read_token(self) -> Result[SecretStr | None, ModelsTokenKeyringLockedError]:
        """The token, or ``None`` when none is stored or there is no Secret Service (the ambient
        login is then used). A locked keyring is a failure; nothing is ever prompted."""
        ...


class CacheProbe(Protocol):
    """Whether the models are completely cached. Offline, blocking, bounded; never raises."""

    def check(self) -> CacheStatus: ...


class DownloadRunner(Protocol):
    def run(
        self,
        token: SecretStr | None,
        timeout_seconds: float,
        on_progress: ProgressCallback,
        cancel: CancelSignal,
    ) -> Result[None, MeetingScribeError]:
        """Download the missing models; blocks, so call it from a thread, never the event loop.

        The token goes to the download process and nowhere else. ``None`` means the ambient login.
        Failures: ``ModelsDownloadCancelledError`` when ``cancel`` is set,
        ``ModelsDownloadTimeoutError``, ``ModelsDownloadReportedError`` (the process's own code) or
        ``ModelsDownloadFailedError``.
        """
        ...


class DownloadEvents(Protocol):
    """Observer of a download; called on the download thread."""

    def progress(self, model: str, step: int, total: int) -> None: ...

    def finished(self, state: Outcome, error_code: str) -> None: ...

    def active_changed(self) -> None: ...


class NullDownloadEvents:
    def progress(self, model: str, step: int, total: int) -> None:
        pass

    def finished(self, state: Outcome, error_code: str) -> None:
        pass

    def active_changed(self) -> None:
        pass
