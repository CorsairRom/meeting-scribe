from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.settings import (
    Settings,
    StorageSettings,
    effective_env_files,
    get_settings,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in ("LOG_LEVEL", "STORAGE_MEETINGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
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


def test_meetings_dir_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORAGE_MEETINGS_DIR", "~/Documents/meetings")

    assert Settings().storage.meetings_dir == tmp_path / "home" / "Documents" / "meetings"
    assert StorageSettings(meetings_dir=Path("~/x")).meetings_dir == tmp_path / "home" / "x"


@pytest.mark.parametrize("raw", ["debug", "Warning", "ERROR"])
def test_log_level_is_normalized_to_upper(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("LOG_LEVEL", raw)

    assert Settings().log_level == raw.upper()


@pytest.mark.parametrize("raw", ["verbose", "", "5"])
def test_invalid_log_level_fails_at_load(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("LOG_LEVEL", raw)

    with pytest.raises(ValidationError):
        Settings()


def _write_env(directory: Path, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_no_env_files_reports_empty() -> None:
    assert effective_env_files() == ()


def test_env_loaded_from_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    env_file = _write_env(xdg / "meeting-scribe", "LOG_LEVEL=ERROR\nSTORAGE_MEETINGS_DIR=/xdg/m\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    settings = Settings()

    assert settings.log_level == "ERROR"
    assert settings.storage.meetings_dir == Path("/xdg/m")
    assert effective_env_files() == (env_file,)


def test_xdg_defaults_to_home_dot_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    env_file = _write_env(home / ".config" / "meeting-scribe", "LOG_LEVEL=WARNING\n")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(home))

    assert Settings().log_level == "WARNING"
    assert effective_env_files() == (env_file,)


def test_cwd_env_is_fallback_when_xdg_absent(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "LOG_LEVEL=CRITICAL\n")

    assert Settings().log_level == "CRITICAL"
    assert effective_env_files() == (env_file.resolve(),)


def test_xdg_env_wins_over_cwd_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    env_file = _write_env(xdg / "meeting-scribe", "LOG_LEVEL=ERROR\n")
    _write_env(tmp_path, "LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert Settings().log_level == "ERROR"
    assert effective_env_files() == (env_file,)


def test_process_env_wins_over_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_env(tmp_path, "LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    assert Settings().log_level == "ERROR"


def test_loaded_settings_remember_the_env_file_they_were_built_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert get_settings().env_files == (env_file,)
    assert Settings().env_files == ()  # not built through load(): source unknown
