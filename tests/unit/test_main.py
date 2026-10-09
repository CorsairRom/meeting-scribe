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
