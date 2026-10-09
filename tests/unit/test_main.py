import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from meeting_scribe.main import Application, create_app, run
from meeting_scribe.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in ("LOG_LEVEL", "STORAGE_MEETINGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)  # no .env picked up
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # don't leak settings resolved here into later tests


pytestmark = pytest.mark.usefixtures("restore_root_logger")


def test_create_app_returns_given_settings() -> None:
    settings = Settings(log_level="WARNING")

    app = create_app(settings)

    assert isinstance(app, Application)
    assert app.settings is settings


def test_create_app_configures_logging_from_settings() -> None:
    create_app(Settings(log_level="WARNING"))

    assert logging.getLogger().level == logging.WARNING


def test_create_app_defaults_to_cached_settings() -> None:
    assert create_app().settings is get_settings()


def test_run_logs_startup_and_returns(caplog: pytest.LogCaptureFixture) -> None:
    # configure_logging(force=True) strips root handlers (incl. caplog's), so capture
    # on the module logger, which it leaves alone.
    main_logger = logging.getLogger("meeting_scribe.main")
    main_logger.addHandler(caplog.handler)
    try:
        run()
    finally:
        main_logger.removeHandler(caplog.handler)

    assert any(
        "no transport" in record.getMessage() and record.levelno == logging.INFO
        for record in caplog.records
    )


@pytest.fixture
def main_records(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[logging.LogRecord]]:
    """Capture INFO records of the ``meeting_scribe.main`` logger, restoring it afterwards."""
    main_logger = logging.getLogger("meeting_scribe.main")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    monkeypatch.setattr(main_logger, "level", logging.INFO)
    main_logger.addHandler(handler)
    yield records
    main_logger.removeHandler(handler)


def test_create_app_logs_config_source_without_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("STORAGE_MEETINGS_DIR=/secret-value\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    create_app()

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any(str(env_file) in m for m in messages)
    assert not any("secret-value" in m for m in messages)


def test_create_app_logs_environment_only_when_no_env_file(
    main_records: list[logging.LogRecord],
) -> None:
    create_app()

    assert any("no .env file" in r.getMessage() for r in main_records)


def test_create_app_logs_the_env_file_recorded_when_settings_were_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("LOG_LEVEL=WARNING\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    settings = get_settings()
    assert settings.log_level == "WARNING"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "moved"))  # state changes after loading

    create_app()

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any(str(env_file) in m for m in messages)
    assert not any("no .env file" in m for m in messages)


def test_create_app_logs_injected_settings_without_resolving_env_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    create_app(Settings(log_level="WARNING"))

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any("settings injected by caller" in m for m in messages)
    assert not any(str(env_file) in m for m in messages)
