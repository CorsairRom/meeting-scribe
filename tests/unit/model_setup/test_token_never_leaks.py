"""AC-16 end to end below D-Bus: the real runner and controller, a fake child, a sentinel token.

The token is read from a fake key store, travels to the child's stdin and must appear nowhere
else: not the child's argv or environment, not a log line (even when the child prints it), not an
event, a status or an error.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

from meeting_scribe.model_setup.application.model_setup_controller import ModelSetupController
from meeting_scribe.model_setup.domain.models import CacheStatus
from meeting_scribe.model_setup.infrastructure.subprocess_download_runner import (
    SubprocessDownloadRunner,
)
from tests.unit.model_setup.fakes import SENTINEL, FakeCacheProbe, FakeTokenSource, RecordingEvents

CHILD = Path(__file__).with_name("fake_prefetch_child.py")


def _wired(tmp_path: Path, mode: str) -> tuple[ModelSetupController, RecordingEvents]:
    runner = SubprocessDownloadRunner(
        kill_grace_seconds=1.0,
        command=(sys.executable, str(CHILD)),
        extra_env={"FAKE_CHILD_MODE": mode, "FAKE_CHILD_REPORT": str(tmp_path / "report.json")},
    )
    probe = FakeCacheProbe(CacheStatus(False, False), CacheStatus(True, True))
    controller = ModelSetupController(
        FakeTokenSource(SENTINEL), probe, runner, download_timeout_seconds=30
    )
    events = RecordingEvents()
    controller.subscribe(events)
    return controller, events


@pytest.mark.parametrize("mode", ["ok", "access", "silent_exit", "leaky_stderr"])
def test_the_token_reaches_only_the_childs_stdin(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    controller, events = _wired(tmp_path, mode)

    with caplog.at_level(logging.DEBUG):
        reply = controller.download()
        events.wait_finished()
        status = controller.status()

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["stdin"] == SENTINEL  # it did arrive, on stdin
    assert SENTINEL not in json.dumps(report["argv"])
    assert SENTINEL not in json.dumps(report["env"])
    everywhere_else = [caplog.text, repr(events.events), repr(status), repr(reply)]
    assert all(SENTINEL not in text for text in everywhere_else)
    assert all("hf_AnotherShapedToken99" not in text for text in everywhere_else)
