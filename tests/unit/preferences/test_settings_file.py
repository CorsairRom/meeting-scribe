"""The JSON settings file store (AC-11, AC-14): atomic, private, never blocks the service."""

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "meeting-scribe" / "settings.json"


def _mode(target: Path) -> int:
    return stat.S_IMODE(target.stat().st_mode)


def test_a_missing_file_reads_as_no_values(path: Path) -> None:
    assert JsonSettingsStore(path).read() == Success({})


def test_values_round_trip(path: Path) -> None:
    store = JsonSettingsStore(path)

    assert store.write({"summaries.provider": "cloud", "storage.meetings_dir": "/x"}) == Success(
        None
    )

    assert store.read() == Success({"summaries.provider": "cloud", "storage.meetings_dir": "/x"})
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_permissions(path: Path) -> None:
    JsonSettingsStore(path).write({"summaries.language": "es"}).unwrap()

    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700


def test_a_write_leaves_no_temporary_file(path: Path) -> None:
    JsonSettingsStore(path).write({"summaries.language": "es"}).unwrap()

    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_a_failed_replace_keeps_the_old_file_and_reports_storage_failed(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonSettingsStore(path)
    store.write({"summaries.language": "es"}).unwrap()

    def broken(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    result = store.write({"summaries.language": "fr"})

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_STORAGE_FAILED"
    assert "disk full" not in str(result.failure())
    monkeypatch.undo()
    assert store.read() == Success({"summaries.language": "es"})
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"version": 2, "values": {}}', '{"values": {}}', '{"version": 1}'],
)
def test_a_damaged_file_is_reported_not_raised(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    result = JsonSettingsStore(path).read()

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_FILE_INVALID"


def test_the_error_message_never_holds_file_content(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("/home/someone/private {", encoding="utf-8")

    result = JsonSettingsStore(path).read()

    assert "someone" not in str(result.failure())


def test_a_non_string_value_is_dropped_and_only_its_key_is_logged(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path.parent.mkdir(parents=True)
    values = {"summaries.language": 7, "summaries.provider": "cloud"}
    path.write_text(json.dumps({"version": 1, "values": values}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = JsonSettingsStore(path).read()

    assert result == Success({"summaries.provider": "cloud"})
    assert "summaries.language" in caplog.text
    assert "7" not in caplog.text


def test_the_next_write_moves_a_bad_file_aside(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("broken", encoding="utf-8")
    store = JsonSettingsStore(path)

    store.write({"summaries.language": "es"}).unwrap()

    assert (path.parent / "settings.json.invalid").read_text(encoding="utf-8") == "broken"
    assert store.read() == Success({"summaries.language": "es"})


def test_an_unwritable_folder_is_a_storage_failure(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o500)
    try:
        result = JsonSettingsStore(path).write({"summaries.language": "es"})
    finally:
        path.parent.chmod(0o700)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_STORAGE_FAILED"
