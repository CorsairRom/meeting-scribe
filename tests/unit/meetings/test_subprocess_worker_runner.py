import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    WorkerCancelledError,
    WorkerCrashedError,
    WorkerProtocolError,
    WorkerReportedError,
    WorkerTimeoutError,
)
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    ProgressOrNotice,
    ResultEvent,
    StartedEvent,
    WorkerJob,
)
from meeting_scribe.meetings.infrastructure.subprocess_worker_runner import (
    SubprocessWorkerRunner,
)

FAKE = Path(__file__).parents[2] / "fakes" / "fake_worker.py"
STARTED = {"event": "started", "pid": 1, "device": "cuda/float16"}
PROGRESS = {"event": "progress", "stage": "transcribe", "processed_seconds": 1, "total_seconds": 9}
RESULT = {"event": "result", "artifacts": {"words": "words.json"}, "detected_language": "es"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.folder = tmp_path / "2026-10-09_101500"
        self.folder.mkdir()
        self.events: list[ProgressOrNotice] = []
        self.cancel = threading.Event()
        self.runner = SubprocessWorkerRunner(
            command=(sys.executable, str(FAKE)),
            kill_grace_seconds=0.5,
            result_exit_grace_seconds=0.5,
        )

    def run(
        self,
        scenario: list[dict[str, object]],
        timeout: float = 10.0,
        on_event=None,
    ) -> Result[ResultEvent, MeetingScribeError]:
        (self.folder / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
        job = WorkerJob.build("transcribe", str(self.folder), "cuda", ProcessingParams())

        def collect(event: ProgressOrNotice) -> Result[None, MeetingScribeError]:
            self.events.append(event)
            return on_event(event) if on_event else Success(None)

        return self.runner.run(job, timeout, collect, self.cancel)

    def pid(self) -> int:
        return int((self.folder / "pid").read_text(encoding="utf-8"))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def harness(tmp_path: Path):
    h = Harness(tmp_path)
    yield h
    pid_file = h.folder / "pid"
    if pid_file.exists() and _alive(h.pid()):
        os.kill(h.pid(), 9)  # a test failed before the runner reaped its worker: kill by PID


def test_a_successful_worker_streams_its_events_then_returns_the_result(harness: Harness) -> None:
    result = harness.run([{"emit": STARTED}, {"emit": PROGRESS}, {"emit": RESULT}, {"exit": 0}])

    event = result.unwrap()
    assert (event.artifacts, event.detected_language) == ({"words": "words.json"}, "es")
    assert harness.events == [
        StartedEvent(pid=1, device="cuda/float16"),
        ProgressEvent(stage="transcribe", processed_seconds=1, total_seconds=9),
    ]


def test_the_job_spec_reaches_the_worker_on_stdin(harness: Harness) -> None:
    harness.run([{"echo_job": True}, {"emit": RESULT}])

    assert harness.events == [CacheHitEvent(artifact="transcribe:1")]


def test_the_child_environment_opts_out_of_every_telemetry(harness: Harness) -> None:
    names = ["HF_HUB_DISABLE_TELEMETRY", "PYANNOTE_METRICS_ENABLED"]

    harness.run([{"echo_env": names}, {"emit": RESULT}])

    assert harness.events == [
        CacheHitEvent(artifact="HF_HUB_DISABLE_TELEMETRY=1,PYANNOTE_METRICS_ENABLED=false")
    ]


def test_the_child_runs_offline_whatever_the_user_environment_says(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # models are fetched at install time only (OQ-7)

    harness.run([{"echo_env": ["HF_HUB_OFFLINE"]}, {"emit": RESULT}])

    assert harness.events == [CacheHitEvent(artifact="HF_HUB_OFFLINE=1")]


def test_the_child_never_looks_up_an_implicit_token_whatever_the_user_environment_says(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "0")  # processing is token-free (ADR-011)

    harness.run([{"echo_env": ["HF_HUB_DISABLE_IMPLICIT_TOKEN"]}, {"emit": RESULT}])

    assert harness.events == [CacheHitEvent(artifact="HF_HUB_DISABLE_IMPLICIT_TOKEN=1")]


def test_an_error_event_with_exit_1_is_the_workers_own_failure(harness: Harness) -> None:
    result = harness.run(
        [
            {"emit": {"event": "error", "error_code": "GPU_OUT_OF_MEMORY", "message": "oom"}},
            {"exit": 1},
        ]
    )

    error = result.failure()
    assert isinstance(error, WorkerReportedError)
    assert (error.error_code, str(error)) == ("GPU_OUT_OF_MEMORY", "oom")


def test_crash_and_timeout_fail_the_job(harness: Harness) -> None:
    crashed = harness.run(
        [{"emit": PROGRESS}, {"stderr": "CUDA exploded"}, {"kill_self": 9}]
    ).failure()

    assert isinstance(crashed, WorkerCrashedError)
    assert "-9" in str(crashed)
    assert "CUDA exploded" in str(crashed)

    started = time.monotonic()
    timed_out = harness.run([{"pidfile": "pid"}, {"hang": True}], timeout=0.5).failure()

    assert isinstance(timed_out, WorkerTimeoutError)
    assert timed_out.error_code == "WORKER_TIMEOUT"
    assert time.monotonic() - started < 5
    assert not _alive(harness.pid())


def test_a_worker_ignoring_sigterm_is_killed_after_the_grace_period(harness: Harness) -> None:
    started = time.monotonic()

    failure = harness.run(
        [{"ignore_sigterm": True}, {"pidfile": "pid"}, {"hang": True}], timeout=0.3
    ).failure()

    elapsed = time.monotonic() - started
    assert isinstance(failure, WorkerTimeoutError)
    assert 0.3 + 0.5 <= elapsed < 5  # timeout, then the 0.5 s grace, then SIGKILL
    assert not _alive(harness.pid())


ERROR_EVENT = {"event": "error", "error_code": "X", "message": "m"}


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ([{"raw": "this is not json"}, {"emit": RESULT}], WorkerProtocolError),
        ([{"emit": {"event": "mystery"}}, {"emit": RESULT}], WorkerProtocolError),
        ([{"emit": RESULT}, {"emit": PROGRESS}, {"exit": 0}], WorkerProtocolError),
        ([{"emit": PROGRESS}, {"exit": 0}], WorkerProtocolError),
        ([{"emit": RESULT}, {"exit": 3}], WorkerCrashedError),
        ([{"emit": ERROR_EVENT}, {"exit": 0}], WorkerProtocolError),
        ([{"emit": RESULT}, {"emit": ERROR_EVENT}, {"exit": 1}], WorkerProtocolError),
    ],
    ids=[
        "non-json",
        "unknown-event",
        "event-after-result",
        "no-final-event",
        "result-bad-exit",
        "error-exit-0",
        "result-and-error",
    ],
)
def test_protocol_violations_fail_the_job_with_their_exact_error(
    harness: Harness, scenario: list[dict[str, object]], expected: type[MeetingScribeError]
) -> None:
    failure = harness.run([{"pidfile": "pid"}, *scenario, {"hang": True}], timeout=5).failure()

    assert type(failure) is expected
    assert not _alive(harness.pid())


def test_a_malformed_line_is_a_protocol_error_and_stops_the_worker(harness: Harness) -> None:
    failure = harness.run([{"pidfile": "pid"}, {"raw": "garbage"}, {"hang": True}]).failure()

    assert isinstance(failure, WorkerProtocolError)
    assert not _alive(harness.pid())


def test_exit_1_without_an_error_event_is_a_crash_with_the_stderr_tail(harness: Harness) -> None:
    failure = harness.run([{"stderr": "Traceback: boom"}, {"exit": 1}]).failure()

    assert isinstance(failure, WorkerCrashedError)
    assert "1" in str(failure)
    assert "boom" in str(failure)


def test_worker_stderr_is_appended_to_processing_log(harness: Harness) -> None:
    harness.run([{"stderr": "first"}, {"emit": RESULT}])
    harness.run([{"stderr": "second"}, {"emit": RESULT}])

    log = (harness.folder / "processing.log").read_text(encoding="utf-8")
    assert log.index("first") < log.index("second")


def test_cancel_stops_a_running_worker(harness: Harness) -> None:
    threading.Timer(0.3, harness.cancel.set).start()

    failure = harness.run([{"pidfile": "pid"}, {"hang": True}]).failure()

    assert isinstance(failure, WorkerCancelledError)
    assert not _alive(harness.pid())


def test_cancel_before_the_run_spawns_nothing(harness: Harness) -> None:
    harness.cancel.set()

    failure = harness.run([{"pidfile": "pid"}, {"emit": RESULT}]).failure()

    assert isinstance(failure, WorkerCancelledError)
    assert not (harness.folder / "pid").exists()


def test_a_failing_event_handler_stops_the_worker_with_its_failure(harness: Harness) -> None:
    rejection = WorkerProtocolError("out of order")

    failure = harness.run(
        [{"pidfile": "pid"}, {"emit": STARTED}, {"hang": True}],
        on_event=lambda event: Failure(rejection),
    ).failure()

    assert failure is rejection
    assert not _alive(harness.pid())


def test_a_command_that_cannot_start_is_a_crash(tmp_path: Path) -> None:
    folder = tmp_path / "m"
    folder.mkdir()
    runner = SubprocessWorkerRunner(command=("/nonexistent/python",), kill_grace_seconds=0.5)
    job = WorkerJob.build("transcribe", str(folder), "cuda", ProcessingParams())

    failure = runner.run(job, 5, lambda e: Success(None), threading.Event()).failure()

    assert isinstance(failure, WorkerCrashedError)


def test_a_worker_that_hangs_after_its_result_is_stopped_and_the_result_kept(
    harness: Harness,
) -> None:
    started = time.monotonic()

    result = harness.run([{"pidfile": "pid"}, {"emit": RESULT}, {"hang": True}], timeout=30)

    assert result.unwrap().detected_language == "es"
    assert time.monotonic() - started < 10
    assert not _alive(harness.pid())


def test_a_worker_that_hangs_after_its_error_event_reports_that_error(harness: Harness) -> None:
    failure = harness.run(
        [{"pidfile": "pid"}, {"emit": ERROR_EVENT}, {"hang": True}], timeout=30
    ).failure()

    assert isinstance(failure, WorkerReportedError)
    assert not _alive(harness.pid())


def test_a_stdin_write_failure_after_the_spawn_stops_the_worker(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_popen = subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    class BrokenStdin:
        def __init__(self, real: object) -> None:
            self._real = real

        def write(self, data: bytes) -> int:
            raise OSError(5, "Input/output error")

        def close(self) -> None:
            self._real.close()  # type: ignore[attr-defined]

    def popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[call-overload]
        process.stdin = BrokenStdin(process.stdin)  # type: ignore[assignment]
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)

    failure = harness.run([{"hang": True}], timeout=5).failure()

    assert isinstance(failure, WorkerCrashedError)
    assert spawned[0].poll() is not None


def test_a_result_received_before_the_deadline_survives_the_deadline_passing_in_the_grace(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.runner = SubprocessWorkerRunner(
        command=(sys.executable, str(FAKE)), kill_grace_seconds=0.5, result_exit_grace_seconds=5.0
    )

    result = h.run([{"pidfile": "pid"}, {"emit": RESULT}, {"hang": True}], timeout=1.5)

    assert result.unwrap().detected_language == "es"  # the deadline passed while it lingered
    assert not _alive(h.pid())
