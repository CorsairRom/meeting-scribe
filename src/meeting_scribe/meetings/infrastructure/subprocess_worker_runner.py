"""Runs one GPU stage in a worker subprocess and speaks the JSON-lines protocol (spec 6.1, 6.2).

``run`` blocks, so the processing loop calls it from its own thread (never the D-Bus event loop).
The worker is its own process-group leader: termination signals the group by id, never by name
(GO-007). A reader thread feeds stdout lines into a queue so timeout and cancellation are checked
while the worker is silent.
"""

import contextlib
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    WorkerCancelledError,
    WorkerCrashedError,
    WorkerProtocolError,
    WorkerReportedError,
    WorkerTimeoutError,
)
from meeting_scribe.meetings.domain.ports import CancelSignal
from meeting_scribe.meetings.domain.worker import (
    ProgressOrNotice,
    ResultEvent,
    WorkerErrorEvent,
    WorkerJob,
    parse_event,
)

logger = logging.getLogger(__name__)

LOG_NAME = "processing.log"
DEFAULT_COMMAND = (sys.executable, "-m", "meeting_scribe.worker")
_POLL_SECONDS = 0.05
_REAP_SECONDS = 5.0
_RESULT_EXIT_GRACE_SECONDS = 10.0  # how long a worker may linger after its final event
_STDERR_TAIL_CHARS = 1000
_CHILD_ENV = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "PYANNOTE_METRICS_ENABLED": "false",
    # Models are downloaded by ``make install`` (OQ-7); processing never touches the network.
    "HF_HUB_OFFLINE": "1",
    # Processing is token-free (ADR-011): ``token=None`` must not fall back to HF_TOKEN or the
    # stored login file.
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
}

type _OnEvent = Callable[[ProgressOrNotice], Result[None, MeetingScribeError]]


class SubprocessWorkerRunner:
    def __init__(
        self,
        kill_grace_seconds: float,
        result_exit_grace_seconds: float = _RESULT_EXIT_GRACE_SECONDS,
        command: Sequence[str] = DEFAULT_COMMAND,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        """``kill_grace_seconds`` comes from ``ProcessingSettings`` (single source of defaults)."""
        self._command = tuple(command)
        self._grace = kill_grace_seconds
        self._result_exit_grace = result_exit_grace_seconds
        self._env = {**os.environ, **_CHILD_ENV, **(extra_env or {})}

    def run(
        self,
        job: WorkerJob,
        timeout_seconds: float,
        on_event: _OnEvent,
        cancel: CancelSignal,
    ) -> Result[ResultEvent, MeetingScribeError]:
        if cancel.is_set():
            return Failure(WorkerCancelledError(f"The {job.stage} worker was cancelled."))
        log_path = Path(job.meeting_dir) / LOG_NAME
        spawned = self._spawn(job, log_path)
        if isinstance(spawned, Failure):
            return spawned
        process, log_offset = spawned.unwrap()
        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True)
        reader.start()
        try:
            return self._supervise(
                job,
                process,
                lines,
                time.monotonic() + timeout_seconds,
                on_event,
                cancel,
                log_path,
                log_offset,
            )
        finally:
            self._terminate(process)  # no-op when it already exited
            reader.join(timeout=1.0)

    def _spawn(
        self, job: WorkerJob, log_path: Path
    ) -> Result[tuple[subprocess.Popen[bytes], int], WorkerCrashedError]:
        try:
            log_offset = log_path.stat().st_size if log_path.exists() else 0
            with log_path.open("ab") as log:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
                    [*self._command, job.stage],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    env=self._env,
                    start_new_session=True,
                )
            assert process.stdin is not None  # noqa: S101 - PIPE was requested
            try:
                process.stdin.write(job.to_json().encode("utf-8"))
                process.stdin.close()
            except BrokenPipeError:
                pass  # the worker died early; its exit status explains why
            except OSError:
                self._terminate(process)  # alive and unsupervised otherwise
                raise
        except OSError as error:
            return Failure(WorkerCrashedError(f"Cannot start the {job.stage} worker: {error}"))
        return Success((process, log_offset))

    def _supervise(
        self,
        job: WorkerJob,
        process: subprocess.Popen[bytes],
        lines: "queue.Queue[str | None]",
        deadline: float,
        on_event: _OnEvent,
        cancel: CancelSignal,
        log_path: Path,
        log_offset: int,
    ) -> Result[ResultEvent, MeetingScribeError]:
        run = _Run()
        while True:
            interruption = _interruption(job, deadline, cancel)
            if isinstance(interruption, WorkerTimeoutError) and run.final_at is not None:
                # The final event arrived in time; the deadline passed while the worker lingered.
                self._terminate(process)
                return self._conclude(run)
            if interruption is not None:
                return self._stopped(process, interruption)
            if run.lingering(self._result_exit_grace):
                logger.warning(
                    "Worker pid %d outlived its final event by %.1f s; stopping it.",
                    process.pid,
                    self._result_exit_grace,
                )
                self._terminate(process)
                return self._conclude(run)
            if run.eof:
                if process.poll() is not None:
                    break
                time.sleep(_POLL_SECONDS)
                continue
            failure = _pump(lines, on_event, run)
            if failure is not None:
                return self._stopped(process, failure)
        return self._outcome(job, process.returncode, run, log_path, log_offset)

    @staticmethod
    def _conclude(run: "_Run") -> Result[ResultEvent, MeetingScribeError]:
        """The worker said its last word and then hung: keep what it said."""
        if run.result is not None:
            return Success(run.result)
        assert run.reported is not None  # noqa: S101 - lingering() needs a final event
        return Failure(WorkerReportedError(run.reported.error_code, run.reported.message))

    def _outcome(
        self,
        job: WorkerJob,
        code: int,
        run: "_Run",
        log_path: Path,
        log_offset: int,
    ) -> Result[ResultEvent, MeetingScribeError]:
        result, reported = run.result, run.reported
        if code == 0:
            if result is not None:
                return Success(result)
            return Failure(
                WorkerProtocolError(f"The {job.stage} worker exited 0 without a result.")
            )
        if code == 1 and reported is not None:
            return Failure(WorkerReportedError(reported.error_code, reported.message))
        tail = _stderr_tail(log_path, log_offset)
        return Failure(
            WorkerCrashedError(f"The {job.stage} worker exited with code {code}. stderr: {tail}")
        )

    def _stopped(
        self, process: subprocess.Popen[bytes], error: MeetingScribeError
    ) -> Result[ResultEvent, MeetingScribeError]:
        self._terminate(process)
        return Failure(error)

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        """SIGTERM the process group, wait the grace period, then SIGKILL; bounded throughout."""
        if process.poll() is not None:
            return
        _signal_group(process, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=self._grace)
        if process.poll() is not None:
            return
        logger.warning("Worker pid %d ignored SIGTERM; sending SIGKILL.", process.pid)
        _signal_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            logger.error("Worker pid %d is still alive after SIGKILL.", process.pid)


class _Run:
    """What one supervised run has seen so far."""

    def __init__(self) -> None:
        self.result: ResultEvent | None = None
        self.reported: WorkerErrorEvent | None = None
        self.eof = False
        self.final_at: float | None = None  # when the result or error event arrived

    def lingering(self, grace: float) -> bool:
        return self.final_at is not None and time.monotonic() - self.final_at > grace


def _interruption(
    job: WorkerJob, deadline: float, cancel: CancelSignal
) -> WorkerCancelledError | WorkerTimeoutError | None:
    if cancel.is_set():
        return WorkerCancelledError(f"The {job.stage} worker was cancelled.")
    if time.monotonic() >= deadline:
        return WorkerTimeoutError(f"The {job.stage} worker exceeded its timeout.")
    return None


def _pump(
    lines: "queue.Queue[str | None]", on_event: _OnEvent, run: _Run
) -> MeetingScribeError | None:
    """Handle at most one stdout line; a returned error means 'stop the worker with this'."""
    try:
        line = lines.get(timeout=_POLL_SECONDS)
    except queue.Empty:
        return None
    if line is None:
        run.eof = True
        return None
    parsed = parse_event(line.rstrip("\n"))
    if isinstance(parsed, Failure):
        return parsed.failure()
    event = parsed.unwrap()
    if run.result is not None or run.reported is not None:
        return WorkerProtocolError("The worker sent an event after its final event.")
    if isinstance(event, ResultEvent):
        run.result = event
        run.final_at = time.monotonic()
    elif isinstance(event, WorkerErrorEvent):
        run.reported = event
        run.final_at = time.monotonic()
    elif isinstance(handled := on_event(event), Failure):
        return handled.failure()
    return None


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, sig)  # the worker leads its own group (start_new_session)


def _read_lines(stream: IO[bytes] | None, lines: "queue.Queue[str | None]") -> None:
    try:
        if stream is not None:
            for raw in stream:
                lines.put(raw.decode("utf-8", errors="replace"))
    finally:
        lines.put(None)


def _stderr_tail(log_path: Path, offset: int) -> str:
    try:
        with log_path.open("rb") as handle:
            handle.seek(offset)
            text = handle.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return "(no log)"
    return text[-_STDERR_TAIL_CHARS:] or "(empty)"
