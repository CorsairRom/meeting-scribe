"""``prefetch-models --check`` against a FAKE hub cache in a temporary HOME (never the real one).

The real ``huggingface_hub`` and ``faster_whisper`` run in a subprocess whose HOME, cache and token
path all point under ``tmp_path``, offline, with no token: it proves the check needs neither the
network nor a login, and that a half-downloaded model does not count.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.hf_fakes import make_snapshot

SRC = Path(__file__).resolve().parents[2] / "src"
WHISPER_REPO = "mobiuslabsgmbh--faster-whisper-large-v3-turbo"
PIPELINE_REPO = "pyannote--speaker-diarization-community-1"

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None
    or importlib.util.find_spec("huggingface_hub") is None,
    reason="the ml extra is not installed",
)


def _cache(tmp_path: Path) -> tuple[Path, Path]:
    hub = tmp_path / "hub"
    whisper = make_snapshot(
        hub,
        {
            "config.json": b"{}",
            "preprocessor_config.json": b"{}",
            "model.bin": b"x",
            "tokenizer.json": b"{}",
            "vocabulary.json": b"[]",
        },
        repo=WHISPER_REPO,
    )
    pipeline = make_snapshot(
        hub,
        {"config.yaml": b"embedding: $model/embedding\n", "embedding/weights.bin": b"x"},
        repo=PIPELINE_REPO,
    )
    return whisper, pipeline


def _check(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "HF_HUB_CACHE": str(tmp_path / "hub"),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_TOKEN_PATH": str(tmp_path / "no-token-here"),
    }
    return subprocess.run(
        [sys.executable, "-m", "meeting_scribe.cli", "prefetch-models", "--check"],
        capture_output=True,
        text=True,
        env=env,
        cwd=home,
        timeout=120,
        check=False,
    )


def test_both_models_cached_is_ready_offline_and_without_a_token(tmp_path: Path) -> None:
    _cache(tmp_path)

    done = _check(tmp_path)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"whisper": True, "pipeline": True}


def test_an_empty_cache_is_pending(tmp_path: Path) -> None:
    (tmp_path / "hub").mkdir()

    done = _check(tmp_path)

    assert done.returncode == 1
    assert json.loads(done.stdout) == {"whisper": False, "pipeline": False}


def test_a_half_downloaded_pipeline_is_not_cached(tmp_path: Path) -> None:
    _, pipeline = _cache(tmp_path)
    repo = pipeline.parents[1]
    (repo / "blobs" / "blob1").unlink()  # the weights never arrived
    (repo / "blobs" / "fff.incomplete").write_bytes(b"half")

    done = _check(tmp_path)

    assert done.returncode == 1
    assert json.loads(done.stdout) == {"whisper": True, "pipeline": False}
