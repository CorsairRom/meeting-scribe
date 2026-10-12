"""``ModelSetupSettings`` (``MODEL_SETUP_*``): the one knob, the download timeout."""

import pytest
from pydantic import ValidationError

from meeting_scribe.model_setup.infrastructure.model_setup_settings import ModelSetupSettings
from meeting_scribe.settings import Settings


def test_the_default_timeout_is_one_hour() -> None:
    assert ModelSetupSettings().download_timeout_seconds == 3600


def test_the_environment_overrides_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS", "120.5")

    assert ModelSetupSettings().download_timeout_seconds == 120.5
    assert Settings().model_setup.download_timeout_seconds == 120.5


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_a_timeout_must_be_a_positive_number(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        ModelSetupSettings()
