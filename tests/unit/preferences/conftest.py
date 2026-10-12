"""Every preferences test runs against tmp XDG folders and a clean environment (BR-002, safety)."""

from pathlib import Path

import pytest

_VARS = (
    "SUMMARIES_PROVIDER",
    "SUMMARIES_LOCAL_MODEL",
    "SUMMARIES_LANGUAGE",
    "SUMMARIES_CLOUD_MODEL",
    "SUMMARIES_CLOUD_ENABLED",
    "STORAGE_MEETINGS_DIR",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
