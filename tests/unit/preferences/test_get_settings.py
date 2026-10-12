"""``GetAll`` rows: value, default, winning source, lock and apply mode (AC-1, AC-10, AC-11)."""

from pathlib import Path

import pytest

from tests.unit.preferences.fakes import Service

KEYS = [
    "summaries.provider",
    "summaries.local_model",
    "summaries.language",
    "summaries.cloud_model",
    "storage.meetings_dir",
]


def _rows(service: Service) -> dict:
    return {row.key: row for row in service.service.get_all()}


def test_one_row_per_exposed_field_with_the_default_source(tmp_path: Path) -> None:
    rows = service_rows = Service(tmp_path).service.get_all()

    assert [row.key for row in rows] == KEYS
    assert all((row.source, row.locked, row.lock_reason) == ("default", False, "") for row in rows)
    assert all(row.value == row.default for row in service_rows)
    assert [(r.kind, r.apply) for r in rows] == [
        ("choice", "live"),
        ("text", "live"),
        ("choice", "live"),
        ("text", "live"),
        ("path", "restart"),
    ]
    assert rows[0].choices == ("local", "cloud")
    assert rows[1].choices == ()
    assert rows[0].file_problem == ""


def test_a_saved_value_has_the_file_source_and_is_not_locked(tmp_path: Path) -> None:
    rows = _rows(Service(tmp_path, {"summaries.local_model": "m-file"}))

    row = rows["summaries.local_model"]
    assert (row.value, row.source, row.locked) == ("m-file", "file", False)
    assert row.default == "qwen3.5:9b"


def test_the_environment_locks_the_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")

    row = _rows(Service(tmp_path, {"summaries.local_model": "m-file"}))["summaries.local_model"]

    assert (row.value, row.source, row.locked) == ("m-env", "env", True)


def test_dotenv_also_locks_the_field(tmp_path: Path) -> None:
    folder = tmp_path / "xdg" / "meeting-scribe"
    folder.mkdir(parents=True)
    (folder / ".env").write_text("SUMMARIES_LANGUAGE=es\n", encoding="utf-8")

    row = _rows(Service(tmp_path))["summaries.language"]

    assert (row.value, row.source, row.locked) == ("es", "dotenv", True)


def test_the_legacy_switch_locks_the_provider_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SUMMARIES_CLOUD_ENABLED", "false")

    row = _rows(Service(tmp_path, {"summaries.provider": "cloud"}))["summaries.provider"]

    assert (row.value, row.locked, row.lock_reason) == ("local", True, "LEGACY_SETTING")


def test_a_damaged_file_is_reported_on_every_row(tmp_path: Path) -> None:
    rows = Service(tmp_path, file_problem="SETTINGS_FILE_INVALID").service.get_all()

    assert {row.file_problem for row in rows} == {"SETTINGS_FILE_INVALID"}
