"""``SubprocessCacheProbe``: ``prefetch-models --check`` run as a child, offline and bounded."""

import json
import sys
from pathlib import Path

from meeting_scribe.model_setup.domain.models import CacheStatus
from meeting_scribe.model_setup.infrastructure.subprocess_cache_probe import (
    DEFAULT_COMMAND,
    SubprocessCacheProbe,
)

CHILD = Path(__file__).with_name("fake_prefetch_child.py")


def _probe(tmp_path: Path, timeout: float = 20.0, **env: str) -> SubprocessCacheProbe:
    return SubprocessCacheProbe(
        timeout_seconds=timeout,
        command=(sys.executable, str(CHILD)),
        extra_env={"FAKE_CHILD_REPORT": str(tmp_path / "report.json"), **env},
    )


def test_the_default_command_is_the_check_option_of_the_cli_module() -> None:
    assert (sys.executable, "-m", "meeting_scribe.cli", "prefetch-models") == DEFAULT_COMMAND


def test_both_cached(tmp_path: Path) -> None:
    assert _probe(tmp_path).check() == CacheStatus(True, True)


def test_a_pending_pipeline_is_read_from_the_json_line_even_though_the_exit_is_one(
    tmp_path: Path,
) -> None:
    probe = _probe(
        tmp_path, FAKE_CHECK_OUTPUT='{"whisper": true, "pipeline": false}', FAKE_CHECK_EXIT="1"
    )

    assert probe.check() == CacheStatus(True, False)


def test_the_check_runs_offline_with_no_token_and_no_stdin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "ambient")
    _probe(tmp_path).check()

    seen = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert seen["argv"] == ["--check"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert "HF_TOKEN" not in seen["env"]
    assert seen["stdin"] is None


def test_garbage_output_means_not_cached(tmp_path: Path) -> None:
    assert _probe(tmp_path, FAKE_CHECK_OUTPUT="not json").check() == CacheStatus(False, False)
    assert _probe(tmp_path, FAKE_CHECK_OUTPUT='{"whisper": 1}').check() == CacheStatus(False, False)


def test_a_missing_executable_means_not_cached() -> None:
    probe = SubprocessCacheProbe(timeout_seconds=5.0, command=("/nonexistent/binary",))

    assert probe.check() == CacheStatus(False, False)


def test_a_probe_that_outlives_its_bound_means_not_cached() -> None:
    probe = SubprocessCacheProbe(
        timeout_seconds=0.5,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )

    assert probe.check() == CacheStatus(False, False)
