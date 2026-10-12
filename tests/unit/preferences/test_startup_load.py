"""A damaged settings file never blocks the service (AC-11)."""

import json
import logging
from pathlib import Path

import pytest

from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore
from meeting_scribe.settings import load_saved_settings


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "meeting-scribe" / "settings.json"


def _write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document), "utf-8")


def test_no_file_starts_with_defaults(path: Path) -> None:
    loaded = load_saved_settings(JsonSettingsStore(path))

    assert (loaded.saved, loaded.file_problem) == ({}, "")
    assert loaded.settings.summaries.provider == "local"


def test_saved_values_are_loaded_below_the_environment(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        path, {"version": 1, "values": {"summaries.language": "es", "summaries.cloud_model": "m"}}
    )
    monkeypatch.setenv("SUMMARIES_CLOUD_MODEL", "m-env")

    loaded = load_saved_settings(JsonSettingsStore(path))

    assert loaded.settings.summaries.language == "es"
    assert loaded.settings.summaries.cloud_model == "m-env"


def test_a_file_that_is_not_json_is_ignored_logged_by_code_and_reported(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(path, "/home/someone/private {")

    with caplog.at_level(logging.WARNING):
        loaded = load_saved_settings(JsonSettingsStore(path))

    assert loaded.file_problem == "SETTINGS_FILE_INVALID"
    assert loaded.saved == {}
    assert loaded.settings.summaries.provider == "local"
    assert "SETTINGS_FILE_INVALID" in caplog.text
    assert "someone" not in caplog.text


def test_an_unknown_version_is_ignored(path: Path) -> None:
    _write(path, {"version": 9, "values": {"summaries.language": "es"}})

    loaded = load_saved_settings(JsonSettingsStore(path))

    assert (loaded.file_problem, loaded.saved) == ("SETTINGS_FILE_INVALID", {})


def test_one_invalid_value_falls_back_alone_and_only_its_key_is_logged(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(
        path,
        {
            "version": 1,
            "values": {
                "summaries.language": "Not Valid Value",
                "summaries.cloud_model": "glm-ok",
                "summaries.unknown_field": "x",
            },
        },
    )

    with caplog.at_level(logging.WARNING):
        loaded = load_saved_settings(JsonSettingsStore(path))

    assert loaded.settings.summaries.language == "auto"
    assert loaded.settings.summaries.cloud_model == "glm-ok"
    assert loaded.saved == {"summaries.cloud_model": "glm-ok"}
    assert loaded.file_problem == ""
    assert "summaries.language" in caplog.text
    assert "Not Valid Value" not in caplog.text
