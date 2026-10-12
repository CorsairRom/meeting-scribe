"""Every preferences test runs against tmp XDG folders and a clean environment (BR-002, safety)."""

from pathlib import Path

import pytest

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY

_VARS = (
    *(f"{spec.section}_{spec.name}".upper() for spec in FIELD_REGISTRY),
    "SUMMARIES_CLOUD_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
