"""Fakes for the model_setup ports: no child process, no keyring, no network, no model."""

import threading
import time
from collections.abc import Callable

from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadCancelledError,
    ModelsTokenKeyringLockedError,
)
from meeting_scribe.model_setup.domain.models import CacheStatus
from meeting_scribe.model_setup.domain.ports import CancelSignal, ProgressCallback

SENTINEL = "hf_SENTINELtoken0123456789"


class FakeTokenSource:
    def __init__(
        self,
        token: str | None = None,
        *,
        locked: bool = False,
        stored: bool | None = None,
    ) -> None:
        self._token = token
        self._locked = locked
        self._stored = (token is not None or locked) if stored is None else stored
        self.reads = 0

    def token_stored(self) -> bool:
        return self._stored

    def read_token(self) -> Result[SecretStr | None, ModelsTokenKeyringLockedError]:
        self.reads += 1
        if self._locked:
            return Failure(ModelsTokenKeyringLockedError("The GNOME Keyring is locked."))
        return Success(SecretStr(self._token) if self._token else None)


class FakeCacheProbe:
    """Answers from a script: each ``check`` pops the next status (the last one repeats)."""

    def __init__(self, *statuses: CacheStatus, gate: threading.Event | None = None) -> None:
        self._statuses = list(statuses) or [CacheStatus(False, False)]
        self.gate = gate  # holds ``check`` until the test releases it (a slow probe)
        self.entered = threading.Event()
        self.calls = 0

    def check(self) -> CacheStatus:
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10), "the slow probe was never released"
        if len(self._statuses) > 1:
            return self._statuses.pop(0)
        return self._statuses[0]


class FakeRunner:
    """Runs a scripted download. ``gate`` (when set) holds it until the test releases it."""

    def __init__(
        self,
        *,
        steps: tuple[tuple[str, int, int], ...] = (("pipeline", 1, 2), ("whisper", 2, 2)),
        failure: MeetingScribeError | None = None,
        gate: threading.Event | None = None,
        ignore_cancel: bool = False,
    ) -> None:
        self.ignore_cancel = ignore_cancel  # finishes as a success even when cancelled (a race)
        self.steps = steps
        self.failure = failure
        self.gate = gate
        self.started = threading.Event()
        self.runs: list[tuple[str | None, float]] = []
        self.tokens: list[SecretStr | None] = []

    def run(
        self,
        token: SecretStr | None,
        timeout_seconds: float,
        on_progress: ProgressCallback,
        cancel: CancelSignal,
    ) -> Result[None, MeetingScribeError]:
        self.tokens.append(token)
        self.runs.append((token.get_secret_value() if token else None, timeout_seconds))
        self.started.set()
        for model, step, total in self.steps:
            on_progress(model, step, total)
        if self.gate is not None:
            while not self.gate.is_set():
                if cancel.is_set() and not self.ignore_cancel:
                    return Failure(ModelsDownloadCancelledError("The download was cancelled."))
                self.gate.wait(0.01)
        if cancel.is_set() and not self.ignore_cancel:
            return Failure(ModelsDownloadCancelledError("The download was cancelled."))
        return Failure(self.failure) if self.failure else Success(None)


class RecordingEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[object, ...]]] = []
        self.finished_event = threading.Event()

    def progress(self, model: str, step: int, total: int) -> None:
        self.events.append(("progress", (model, step, total)))

    def finished(self, state: str, error_code: str) -> None:
        self.events.append(("finished", (state, error_code)))
        self.finished_event.set()

    def active_changed(self) -> None:
        self.events.append(("active_changed", ()))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]

    def wait_finished(self, timeout: float = 5.0) -> None:
        assert self.finished_event.wait(timeout), "the download did not finish"


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "condition not reached"
        time.sleep(0.005)
