"""What the chat adapters share: the outcome of one attempt, the retry loop and the watchdog.

Both the OpenAI-compatible adapter (cloud) and the native Ollama adapter (local) make attempts
that can fail before the stream (retry with backoff), in the middle of it (one retry) or for good.
The retry budget, the interruptible pauses and the cancel/deadline watchdog are the same for both.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx2
from returns.result import Failure, Result

from meeting_scribe.summaries.domain.errors import (
    SummaryCancelledError,
    SummaryError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.ports import CancelSignal, Completion

CONNECT_TIMEOUT_SECONDS = 5.0
HTTP_TRANSIENT = frozenset({408, 409})
HTTP_RATE_LIMITED, HTTP_SERVER_ERROR = 429, 500
_POLL_SECONDS = 0.05
_BACKOFF_START_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
_RETRY_AFTER_MAX_SECONDS = 60.0


@dataclass
class Outcome:
    result: Result[Completion, SummaryError]
    kind: Literal["final", "transient", "midstream"] = "final"
    """``transient`` failed before the stream (retry with backoff); ``midstream`` broke after it."""
    delay: float | None = None
    """The provider's own ``Retry-After`` hint in seconds, when it sent one."""


class Closeable(Protocol):
    def close(self) -> None: ...


def is_transient_status(status: int) -> bool:
    return status in HTTP_TRANSIENT or status == HTTP_RATE_LIMITED or status >= HTTP_SERVER_ERROR


def retry_after(response: httpx2.Response) -> float | None:
    """The provider's ``retry-after-ms`` or ``retry-after`` (seconds) hint; ``None`` if absent.

    A hint that is not a number or lies outside ``0..60`` seconds is ignored, so the caller falls
    back to its own exponential backoff.
    """
    for header, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        try:
            seconds = float(response.headers[header]) / divisor
        except (KeyError, ValueError):
            continue
        if 0 <= seconds <= _RETRY_AFTER_MAX_SECONDS:
            return seconds
    return None


def stopped_outcome(cancel: CancelSignal, watchdog: "Watchdog") -> Outcome | None:
    """A user cancel wins over an expired deadline."""
    if cancel.is_set():
        return Outcome(Failure(SummaryCancelledError("The summary was cancelled.")))
    if watchdog.reason == "deadline":
        return Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
    return None


def run_with_retries(
    attempt: Callable[[], Outcome],
    *,
    max_retries: int,
    cancel: CancelSignal,
    deadline: float | None,
) -> Result[Completion, SummaryError]:
    """Run ``attempt`` until it ends for good.

    ``max_retries`` is a budget per kind: up to that many transient retries, plus at most one
    mid-stream retry (none when it is 0), so a request can make 1 + max_retries + 1 attempts. The
    retries are ours, so that a cancel or the deadline is honoured between attempts.
    """
    transient_retries = 0
    midstream_retried = False
    while True:
        outcome = attempt()
        if outcome.kind == "transient" and transient_retries < max_retries:
            delay = outcome.delay
            if delay is None:
                delay = min(_BACKOFF_START_SECONDS * 2**transient_retries, _BACKOFF_MAX_SECONDS)
            transient_retries += 1
        elif outcome.kind == "midstream" and not midstream_retried and max_retries > 0:
            midstream_retried = True
            delay = 0.0
        else:
            return outcome.result
        stopped = _pause(delay, cancel, deadline)
        if stopped is not None:
            return stopped.result


def _pause(delay: float, cancel: CancelSignal, deadline: float | None) -> Outcome | None:
    """Wait ``delay`` seconds, ending early on a cancel or when the time left runs out."""
    if deadline is not None:
        delay = min(delay, max(deadline - time.monotonic(), 0.0))
    if cancel.wait(delay) or cancel.is_set():
        return Outcome(Failure(SummaryCancelledError("The summary was cancelled.")))
    if deadline is not None and time.monotonic() >= deadline:
        return Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
    return None


class Watchdog:
    """Closes the stream when the user cancels or the time is up (only real time wakes a stall)."""

    def __init__(self, cancel: CancelSignal, deadline: float | None) -> None:
        self._cancel = cancel
        self._deadline = deadline
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._stream: Closeable | None = None
        self.reason: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "Watchdog":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._done.set()
        self._thread.join()

    def attach(self, stream: Closeable) -> None:
        with self._lock:
            self._stream = stream
            if self.reason is not None:
                stream.close()

    def _run(self) -> None:
        while not self._done.is_set():
            wait = _POLL_SECONDS
            if self._deadline is not None:
                wait = min(wait, max(self._deadline - time.monotonic(), 0.0))
            if self._cancel.wait(wait):
                self._fire("cancel")
                return
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._fire("deadline")
                return

    def _fire(self, reason: str) -> None:
        with self._lock:
            self.reason = reason
            if self._stream is not None:
                self._stream.close()
