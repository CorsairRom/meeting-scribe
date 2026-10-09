import os
import subprocess
import sys
from pathlib import Path

import pytest

from meeting_scribe.speakers.infrastructure.pyannote_backend import (
    TELEMETRY_VARIABLE,
    RealPyannoteBackend,
)

SRC = Path(__file__).resolve().parents[3] / "src"


def test_prepare_forces_telemetry_off_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TELEMETRY_VARIABLE, "true")
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)

    RealPyannoteBackend().prepare()

    assert os.environ[TELEMETRY_VARIABLE] == "false"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_versions_come_from_metadata_and_importing_the_backend_loads_no_ml_library() -> None:
    code = (
        "import sys\n"
        "from meeting_scribe.speakers.infrastructure.pyannote_backend import RealPyannoteBackend\n"
        "versions = RealPyannoteBackend().engine_versions()\n"
        "assert set(versions) == {'pyannote.audio', 'torch'}, versions\n"
        "print([m for m in ('torch', 'pyannote', 'pyannote.audio') if m in sys.modules])\n"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv, no input
        [sys.executable, "-c", code],  # no -I: it ignores PYTHONPATH, so only an install would work
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(SRC)},
    ).stdout

    assert out.strip() == "[]"
