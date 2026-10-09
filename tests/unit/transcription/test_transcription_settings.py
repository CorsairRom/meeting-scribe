from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.settings import Settings
from meeting_scribe.transcription.infrastructure.transcription_settings import (
    TranscriptionSettings,
)

_VARS = (
    "TRANSCRIPTION_MODEL",
    "TRANSCRIPTION_LANGUAGE",
    "TRANSCRIPTION_COMPUTE_TYPE",
    "TRANSCRIPTION_CPU_COMPUTE_TYPE",
    "TRANSCRIPTION_BEAM_SIZE",
    "TRANSCRIPTION_VAD_FILTER",
    "TRANSCRIPTION_CONDITION_ON_PREVIOUS_TEXT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)


def test_defaults_follow_the_spec() -> None:
    settings = TranscriptionSettings()

    assert (settings.model, settings.language) == ("large-v3-turbo", "es")
    assert (settings.compute_type, settings.cpu_compute_type) == ("float16", "int8")
    assert (settings.beam_size, settings.vad_filter) == (5, True)
    assert settings.condition_on_previous_text is False


def test_env_override_and_params_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIPTION_LANGUAGE", "auto")
    monkeypatch.setenv("TRANSCRIPTION_BEAM_SIZE", "1")
    monkeypatch.setenv("TRANSCRIPTION_VAD_FILTER", "false")

    params = TranscriptionSettings().params()

    assert (params.language, params.beam_size, params.vad_filter) == ("auto", 1, False)
    assert params.model == "large-v3-turbo"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TRANSCRIPTION_BEAM_SIZE", "0"),
        ("TRANSCRIPTION_LANGUAGE", " "),
        ("TRANSCRIPTION_MODEL", ""),
    ],
)
def test_invalid_values_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        TranscriptionSettings()


def test_root_settings_composes_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIPTION_MODEL", "small")

    assert Settings().transcription.model == "small"
