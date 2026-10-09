from collections.abc import Iterator
from pathlib import Path

import pytest

from meeting_scribe.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in ("LOG_LEVEL", "STORAGE_MEETINGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env picked up
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # don't leak settings resolved here into later tests


def test_defaults() -> None:
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.storage.meetings_dir == Path.home() / "Documents" / "meetings"


def test_env_override_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_MEETINGS_DIR", "/data/custom-meetings")

    settings = Settings()

    assert settings.storage.meetings_dir == Path("/data/custom-meetings")


def test_log_level_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    assert Settings().log_level == "DEBUG"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
