"""The cached pipeline loads OFFLINE with no token source at all (spec install AC-15).

Opt-in: ``uv run pytest -m gpu -s tests/gpu/test_offline_cached_load.py``. It needs the models
already in ``~/.cache/huggingface`` (a previous download) and loads the pipeline on the CPU in a
subprocess. The subprocess has ``HF_TOKEN`` unset, ``HF_HUB_OFFLINE=1``, the implicit token lookup
disabled and ``HF_TOKEN_PATH`` pointing at a file that does not exist, so no token source is
available: a successful load shows the cache needs none. It never downloads, never reads or
prints a token, never touches audio.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gpu

SRC = Path(__file__).resolve().parents[2] / "src"
PIPELINE = os.environ.get(
    "MEETING_SCRIBE_GPU_TEST_PIPELINE", "pyannote/speaker-diarization-community-1"
)
_SCRIPT = """
import sys
from meeting_scribe.speakers.infrastructure.pyannote_backend import RealPyannoteBackend

backend = RealPyannoteBackend()
backend.prepare()
backend.load_pipeline(sys.argv[1], "cpu")
print("loaded")
"""


def test_the_cached_pipeline_loads_offline_with_no_token_source(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ["HOME"],  # only to find the model cache; the token path is redirected
        "PYTHONPATH": str(SRC),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_TOKEN_PATH": str(tmp_path / "no-token-here"),
    }
    assert "HF_TOKEN" not in env

    done = subprocess.run(  # noqa: S603 - fixed argv, offline
        [sys.executable, "-c", _SCRIPT, PIPELINE],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("loaded")
