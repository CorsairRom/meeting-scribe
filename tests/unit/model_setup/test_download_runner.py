"""``SubprocessDownloadRunner``: the token goes only to the child's stdin (AC-16)."""

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from returns.result import Failure, Success

from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadCancelledError,
    ModelsDownloadFailedError,
    ModelsDownloadReportedError,
    ModelsDownloadTimeoutError,
)
from meeting_scribe.model_setup.infrastructure.subprocess_download_runner import (
    DEFAULT_COMMAND,
    SubprocessDownloadRunner,
)
from tests.unit.model_setup.fakes import SENTINEL

CHILD = Path(__file__).with_name("fake_prefetch_child.py")
AMBIENT = "ambient-env-value"  # an obvious fake login token


def _runner(tmp_path: Path, mode: str = "ok", **env: str) -> SubprocessDownloadRunner:
    return SubprocessDownloadRunner(
        kill_grace_seconds=1.0,
        command=(sys.executable, str(CHILD)),
        extra_env={
            "FAKE_CHILD_MODE": mode,
            "FAKE_CHILD_REPORT": str(tmp_path / "report.json"),
            **env,
        },
    )


def _report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))


def _run(runner, token=None, timeout=20.0, cancel=None):
    seen: list[tuple[str, int, int]] = []
    result = runner.run(
        SecretStr(token) if token else None,
        timeout,
        lambda *args: seen.append(args),
        cancel or threading.Event(),
    )
    return result, seen


def test_the_default_command_is_the_cli_module_run_with_the_services_python() -> None:
    assert (sys.executable, "-m", "meeting_scribe.cli", "prefetch-models") == DEFAULT_COMMAND


def test_token_goes_only_to_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", AMBIENT)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", AMBIENT)

    result, _ = _run(_runner(tmp_path), SENTINEL)

    assert result == Success(None)
    seen = _report(tmp_path)
    assert seen["stdin"] == SENTINEL  # one line on standard input
    assert SENTINEL not in " ".join(seen["argv"])  # never argv
    assert SENTINEL not in json.dumps(seen["env"])  # never the environment
    assert "--token-stdin" in seen["argv"]
    assert "--progress-json" in seen["argv"]
    # the stored token wins: the ambient variables are not handed down
    assert "HF_TOKEN" not in seen["env"]
    assert "HUGGING_FACE_HUB_TOKEN" not in seen["env"]


def test_without_a_token_the_child_gets_no_token_option_and_keeps_the_ambient_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", AMBIENT)

    result, _ = _run(_runner(tmp_path), None)

    assert result == Success(None)
    seen = _report(tmp_path)
    assert "--token-stdin" not in seen["argv"]
    assert seen["stdin"] is None
    assert seen["env"]["HF_TOKEN"] == AMBIENT  # the ambient login still works


def test_the_child_may_use_the_network_whatever_the_services_environment_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    _run(_runner(tmp_path), SENTINEL)

    env = _report(tmp_path)["env"]
    assert "HF_HUB_OFFLINE" not in env
    assert "TRANSFORMERS_OFFLINE" not in env
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_progress_lines_become_progress_calls_and_noise_is_ignored(tmp_path: Path) -> None:
    result, seen = _run(_runner(tmp_path, "noise"), SENTINEL)

    assert result == Success(None)
    assert seen == [("pipeline", 1, 2), ("whisper", 2, 2)]


def test_the_childs_own_error_code_is_reported_the_first_one_wins(tmp_path: Path) -> None:
    result, seen = _run(_runner(tmp_path, "access"), SENTINEL)

    error = result.failure()
    assert isinstance(error, ModelsDownloadReportedError)
    assert error.error_code == "DIARIZATION_MODEL_ACCESS_DENIED"
    assert seen == [("pipeline", 1, 2)]


def test_an_exit_without_a_report_is_a_generic_failure_that_quotes_no_output(
    tmp_path: Path,
) -> None:
    result, _ = _run(_runner(tmp_path, "silent_exit"), SENTINEL)

    error = result.failure()
    assert type(error) is ModelsDownloadFailedError
    assert "3" in str(error)  # the exit code, nothing else
    assert SENTINEL not in str(error)


def test_a_timeout_stops_the_child_and_reports_the_timeout(tmp_path: Path) -> None:
    result, _ = _run(_runner(tmp_path, "hang"), SENTINEL, timeout=0.5)

    assert isinstance(result.failure(), ModelsDownloadTimeoutError)


def test_cancel_stops_the_child_and_reports_cancelled(tmp_path: Path) -> None:
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()

    result, _ = _run(_runner(tmp_path, "hang"), SENTINEL, cancel=cancel)

    assert isinstance(result.failure(), ModelsDownloadCancelledError)


def test_a_cancel_set_before_the_start_never_spawns_the_child(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()

    result, _ = _run(_runner(tmp_path), SENTINEL, cancel=cancel)

    assert isinstance(result.failure(), ModelsDownloadCancelledError)
    assert not (tmp_path / "report.json").exists()


def test_the_child_is_gone_after_a_cancel(tmp_path: Path) -> None:
    cancel = threading.Event()
    done = threading.Thread(target=_run, args=(_runner(tmp_path, "hang"), SENTINEL, 60.0, cancel))
    done.start()
    report = tmp_path / "report.json"
    deadline = time.monotonic() + 10
    while not (report.exists() and report.stat().st_size) and time.monotonic() < deadline:
        time.sleep(0.02)  # bounded wait: the child writes its pid before it hangs
    assert report.exists(), "the child never wrote its report"
    time.sleep(0.1)
    pid = _report(tmp_path)["pid"]

    cancel.set()
    done.join(10)

    assert not done.is_alive()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaped: the runner waited for it


def test_a_missing_executable_is_a_generic_failure() -> None:
    runner = SubprocessDownloadRunner(kill_grace_seconds=1.0, command=("/nonexistent/binary",))

    result, _ = _run(runner, SENTINEL)

    assert type(result.failure()) is ModelsDownloadFailedError


def test_child_output_is_scrubbed_before_it_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result, _ = _run(_runner(tmp_path, "leaky_stderr"), SENTINEL)

    assert isinstance(result, Failure)
    assert SENTINEL not in caplog.text
    assert "hf_AnotherShapedToken99" not in caplog.text
    assert "hf_***" in caplog.text


def test_a_token_never_appears_in_any_error_message(tmp_path: Path) -> None:
    for mode in ("access", "silent_exit", "leaky_stderr"):
        result, _ = _run(_runner(tmp_path, mode), SENTINEL)

        assert SENTINEL not in str(result.failure())
        assert SENTINEL not in repr(result)


def test_a_token_straddling_the_logged_tail_is_never_logged_even_in_part(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert len(SENTINEL) == 26  # the child's line is laid out for this length
    with caplog.at_level(logging.DEBUG):
        result, _ = _run(_runner(tmp_path, "straddle"), SENTINEL)

    assert isinstance(result, Failure)
    for start in range(len(SENTINEL) - 5):
        assert SENTINEL[start : start + 6] not in caplog.text  # no 6-character piece survives
    assert "hf_***" in caplog.text
