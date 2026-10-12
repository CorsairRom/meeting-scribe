"""Runs the download as a child process and reads its progress lines (spec install.md 6 and 7).

The child is ``python -m meeting_scribe.cli prefetch-models --token-stdin --progress-json``. The
token, when there is one, is written to its standard input as one line and the pipe is closed: it
is never in argv (visible in ``ps``) and never in the environment (readable through
``/proc/<pid>/environ`` and inherited by grandchildren). Without a keyring token the child runs
without ``--token-stdin`` and falls back to the ambient Hugging Face login. ``run`` blocks, so it
is called from the download thread, never the event loop.

Nothing the child prints reaches a client or an error message: its stdout is parsed for the three
known events, its stderr tail is logged only after the token and anything shaped like one is
scrubbed, and errors carry fixed texts and codes.
"""

import collections
import json
import logging
import queue
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from typing import IO

from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadCancelledError,
    ModelsDownloadFailedError,
    ModelsDownloadReportedError,
    ModelsDownloadTimeoutError,
)
from meeting_scribe.model_setup.domain.ports import CancelSignal, ProgressCallback
from meeting_scribe.model_setup.infrastructure.child_process import (
    DEFAULT_COMMAND,
    child_environment,
    scrub,
    terminate,
)

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_COMMAND", "SubprocessDownloadRunner"]

_POLL_SECONDS = 0.05
_STDERR_TAIL_CHARS = 1000


class _Seen:
    """What the child's stdout has said so far."""

    def __init__(self) -> None:
        self.eof = False
        self.ready = False
        self.error_code = ""  # the first error event wins


class SubprocessDownloadRunner:
    def __init__(
        self,
        kill_grace_seconds: float = 5.0,
        command: Sequence[str] = DEFAULT_COMMAND,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self._grace = kill_grace_seconds
        self._command = tuple(command)
        self._extra_env = dict(extra_env or {})

    def run(
        self,
        token: SecretStr | None,
        timeout_seconds: float,
        on_progress: ProgressCallback,
        cancel: CancelSignal,
    ) -> Result[None, MeetingScribeError]:
        if cancel.is_set():
            return Failure(ModelsDownloadCancelledError("The download was cancelled."))
        secret = token.get_secret_value() if token else None
        spawned = self._spawn(secret)
        if isinstance(spawned, Failure):
            return spawned
        process = spawned.unwrap()
        lines: queue.Queue[str | None] = queue.Queue()
        tail: collections.deque[str] = collections.deque(maxlen=_STDERR_TAIL_CHARS)
        readers = (
            threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True),
            threading.Thread(target=_read_tail, args=(process.stderr, tail), daemon=True),
        )
        for reader in readers:
            reader.start()
        try:
            outcome = self._supervise(
                process, lines, time.monotonic() + timeout_seconds, on_progress, cancel
            )
        finally:
            terminate(process, self._grace)  # no-op when it already exited
            for reader in readers:
                reader.join(timeout=1.0)
        if isinstance(outcome, Failure):
            _log_tail(tail, secret)
        return outcome

    def _spawn(self, secret: str | None) -> Result[subprocess.Popen[bytes], MeetingScribeError]:
        argv = [*self._command]
        if secret is not None:
            argv.append("--token-stdin")
        argv.append("--progress-json")
        env = child_environment(
            offline=False, drop_ambient_token=secret is not None, extra=self._extra_env
        )
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
                argv,
                stdin=subprocess.PIPE if secret is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except OSError as error:
            logger.warning("Cannot start the download process: %s", type(error).__name__)
            return Failure(ModelsDownloadFailedError("The download process could not be started."))
        if secret is not None:
            assert process.stdin is not None  # noqa: S101 - PIPE was requested
            try:
                process.stdin.write(f"{secret}\n".encode())
                process.stdin.close()
            except BrokenPipeError:
                pass  # the child died early; its exit status explains why
            except OSError:
                terminate(process, self._grace)  # alive and unsupervised otherwise
                return Failure(
                    ModelsDownloadFailedError("The download process could not be started.")
                )
        return Success(process)

    def _supervise(
        self,
        process: subprocess.Popen[bytes],
        lines: "queue.Queue[str | None]",
        deadline: float,
        on_progress: ProgressCallback,
        cancel: CancelSignal,
    ) -> Result[None, MeetingScribeError]:
        seen = _Seen()
        while True:
            if cancel.is_set():
                return Failure(ModelsDownloadCancelledError("The download was cancelled."))
            if time.monotonic() >= deadline:
                return Failure(ModelsDownloadTimeoutError("The download took too long."))
            if seen.eof:
                if process.poll() is not None:
                    break
                time.sleep(_POLL_SECONDS)
                continue
            _pump(lines, on_progress, seen)
        return _outcome(process.returncode, seen)


def _outcome(code: int, seen: _Seen) -> Result[None, MeetingScribeError]:
    if code == 0 and seen.ready:
        return Success(None)
    if seen.error_code:
        return Failure(
            ModelsDownloadReportedError(
                seen.error_code, f"The download process reported {seen.error_code}."
            )
        )
    return Failure(ModelsDownloadFailedError(f"The download process exited with code {code}."))


def _pump(lines: "queue.Queue[str | None]", on_progress: ProgressCallback, seen: _Seen) -> None:
    """Handle at most one stdout line. Anything that is not a known event is ignored."""
    try:
        line = lines.get(timeout=_POLL_SECONDS)
    except queue.Empty:
        return
    if line is None:
        seen.eof = True
        return
    try:
        event = json.loads(line)
    except ValueError:
        return
    if isinstance(event, dict):
        _handle(event, on_progress, seen)


def _handle(event: dict[str, object], on_progress: ProgressCallback, seen: _Seen) -> None:
    kind = event.get("event")
    if kind == "step":
        _progress(event, on_progress)
    elif kind == "error":
        code = event.get("error_code")
        if isinstance(code, str) and code and not seen.error_code:
            seen.error_code = code
    elif kind == "finished":
        seen.ready = event.get("ready") is True


def _progress(event: dict[str, object], on_progress: ProgressCallback) -> None:
    model, step, total = event.get("model"), event.get("step"), event.get("total_steps")
    if not (isinstance(model, str) and isinstance(step, int) and isinstance(total, int)):
        return
    try:
        on_progress(model, step, total)
    except Exception:
        logger.warning("A download progress observer failed.", exc_info=True)


def _read_lines(stream: IO[bytes] | None, lines: "queue.Queue[str | None]") -> None:
    try:
        if stream is not None:
            for raw in stream:
                lines.put(raw.decode("utf-8", errors="replace"))
    finally:
        lines.put(None)


def _read_tail(stream: IO[bytes] | None, tail: "collections.deque[str]") -> None:
    if stream is None:
        return
    for raw in stream:  # drained so the child never blocks on a full pipe
        tail.extend(raw.decode("utf-8", errors="replace"))


def _log_tail(tail: "collections.deque[str]", secret: str | None) -> None:
    text = scrub("".join(tail).strip(), secret)
    if text:
        logger.warning("Download process output (scrubbed): %s", text)
