from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.recording.infrastructure.recording_settings import RecordingSettings
from meeting_scribe.settings import Settings

_VARS = (
    "RECORDING_CHUNK_SECONDS",
    "RECORDING_STOP_TIMEOUT_SECONDS",
    "RECORDING_FFMPEG_BINARY",
    "RECORDING_SAMPLE_RATE",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)


def test_defaults() -> None:
    settings = RecordingSettings()

    assert settings.chunk_seconds == 30
    assert settings.stop_timeout_seconds == 10
    assert settings.ffmpeg_binary == "ffmpeg"
    assert settings.sample_rate == 16000


def test_env_override_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORDING_CHUNK_SECONDS", "60")
    monkeypatch.setenv("RECORDING_STOP_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RECORDING_FFMPEG_BINARY", "/opt/ffmpeg")
    monkeypatch.setenv("RECORDING_SAMPLE_RATE", "48000")

    settings = RecordingSettings()

    assert settings.chunk_seconds == 60
    assert settings.stop_timeout_seconds == 2.5
    assert settings.ffmpeg_binary == "/opt/ffmpeg"
    assert settings.sample_rate == 48000


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("RECORDING_CHUNK_SECONDS", "0"),
        ("RECORDING_STOP_TIMEOUT_SECONDS", "0"),
        ("RECORDING_SAMPLE_RATE", "0"),
    ],
)
def test_invalid_values_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        RecordingSettings()


def test_root_settings_composes_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORDING_CHUNK_SECONDS", "45")

    assert Settings().recording.chunk_seconds == 45
