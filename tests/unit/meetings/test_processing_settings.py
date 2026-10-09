from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.meetings.infrastructure.processing_settings import ProcessingSettings
from meeting_scribe.settings import Settings

_VARS = (
    "PROCESSING_DEVICE",
    "PROCESSING_WORKER_TIMEOUT_FACTOR",
    "PROCESSING_WORKER_MIN_TIMEOUT_SECONDS",
    "PROCESSING_WORKER_KILL_GRACE_SECONDS",
    "PROCESSING_NOTIFY",
    "PROCESSING_STORAGE_RETRY_DELAYS_SECONDS",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)


def test_defaults_follow_the_spec() -> None:
    settings = ProcessingSettings()

    assert settings.device == "cuda"
    assert settings.worker_timeout_factor == 1.0
    assert settings.worker_min_timeout_seconds == 900
    assert settings.worker_kill_grace_seconds == 5
    assert settings.notify is True
    assert settings.storage_retry_delays_seconds == (2.0, 8.0, 30.0)


def test_env_override_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSING_DEVICE", "cpu")
    monkeypatch.setenv("PROCESSING_WORKER_TIMEOUT_FACTOR", "2.5")
    monkeypatch.setenv("PROCESSING_WORKER_MIN_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("PROCESSING_WORKER_KILL_GRACE_SECONDS", "1.5")
    monkeypatch.setenv("PROCESSING_NOTIFY", "false")

    settings = ProcessingSettings()

    assert (settings.device, settings.worker_timeout_factor) == ("cpu", 2.5)
    assert (settings.worker_min_timeout_seconds, settings.worker_kill_grace_seconds) == (60, 1.5)
    assert settings.notify is False


def test_storage_retry_delays_come_from_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSING_STORAGE_RETRY_DELAYS_SECONDS", "[1, 5.5]")

    assert ProcessingSettings().storage_retry_delays_seconds == (1.0, 5.5)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PROCESSING_DEVICE", "auto"),
        ("PROCESSING_WORKER_TIMEOUT_FACTOR", "0"),
        ("PROCESSING_WORKER_MIN_TIMEOUT_SECONDS", "0"),
        ("PROCESSING_WORKER_KILL_GRACE_SECONDS", "0"),
        ("PROCESSING_STORAGE_RETRY_DELAYS_SECONDS", "[2, 0]"),
        ("PROCESSING_STORAGE_RETRY_DELAYS_SECONDS", "[]"),
    ],
)
def test_invalid_values_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        ProcessingSettings()


def test_root_settings_composes_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSING_DEVICE", "cpu")

    assert Settings().processing.device == "cpu"
