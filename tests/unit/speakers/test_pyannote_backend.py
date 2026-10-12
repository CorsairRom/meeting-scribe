import os
import subprocess
import sys
import types
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


def _fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


class _Loaded:
    def __init__(self) -> None:
        self.device: object = None

    def to(self, device: object) -> None:
        self.device = device


def _fake_pyannote(monkeypatch: pytest.MonkeyPatch, seen: list[tuple[str, object]]) -> _Loaded:
    loaded = _Loaded()

    class Pipeline:
        @staticmethod
        def from_pretrained(name: str, *, token: object) -> _Loaded:
            seen.append((name, token))
            return loaded

    audio = _fake_module(monkeypatch, "pyannote.audio", Pipeline=Pipeline)
    _fake_module(monkeypatch, "pyannote", audio=audio)
    _fake_module(monkeypatch, "torch", device=lambda name: f"device:{name}")
    return loaded


def test_load_pipeline_passes_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-15: processing loads the cache explicitly with ``token=None``, never the ambient login."""
    seen: list[tuple[str, object]] = []
    loaded = _fake_pyannote(monkeypatch, seen)

    RealPyannoteBackend().load_pipeline("pyannote/x", "cpu")

    assert seen == [("pyannote/x", None)]
    assert loaded.device == "device:cpu"


def test_fetch_pipeline_with_a_token_passes_it_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, object]] = []
    _fake_pyannote(monkeypatch, seen)

    RealPyannoteBackend().fetch_pipeline("pyannote/x", "hf_sentinel")

    assert seen == [("pyannote/x", "hf_sentinel")]


def test_fetch_pipeline_without_a_token_uses_the_ambient_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, object]] = []
    _fake_pyannote(monkeypatch, seen)

    RealPyannoteBackend().fetch_pipeline("pyannote/x")

    assert seen == [("pyannote/x", True)]  # token=True: the stored login, never read here


def _snapshot(root: Path, files: dict[str, bytes]) -> Path:
    """A fake hub snapshot: ``blobs`` holds the content, ``snapshots/<rev>`` links to it."""
    repo = root / "models--fake--pipeline"
    snapshot = repo / "snapshots" / "rev1"
    (repo / "blobs").mkdir(parents=True)
    for index, (name, content) in enumerate(files.items()):
        blob = repo / "blobs" / f"blob{index}"
        blob.write_bytes(content)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(blob)
    return snapshot


def _fake_hub(monkeypatch: pytest.MonkeyPatch, result: object) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def snapshot_download(repo: str, **kwargs: object) -> str:
        calls.append({"repo": repo, **kwargs})
        if isinstance(result, Exception):
            raise result
        return str(result)

    _fake_module(monkeypatch, "huggingface_hub", snapshot_download=snapshot_download)
    return calls


def test_a_complete_cached_pipeline_is_cached_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(
        tmp_path,
        {"config.yaml": b"embedding: $model/embedding\n", "embedding/model.bin": b"x"},
    )
    calls = _fake_hub(monkeypatch, snapshot)

    assert RealPyannoteBackend().is_pipeline_cached("pyannote/x") is True
    assert calls == [{"repo": "pyannote/x", "local_files_only": True}]


def test_a_pipeline_missing_a_referenced_model_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(tmp_path, {"config.yaml": b"embedding: $model/embedding\n"})
    _fake_hub(monkeypatch, snapshot)

    assert RealPyannoteBackend().is_pipeline_cached("pyannote/x") is False


def test_a_pipeline_that_is_not_in_the_cache_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_hub(monkeypatch, type("LocalEntryNotFoundError", (Exception,), {})("nope"))

    assert RealPyannoteBackend().is_pipeline_cached("pyannote/x") is False
