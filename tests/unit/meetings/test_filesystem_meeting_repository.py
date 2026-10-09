import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from returns.result import Failure

from meeting_scribe.meetings.domain.errors import (
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.domain.meeting import (
    NULL_MEETING,
    Meeting,
    ProcessingParams,
    RecordingInfo,
)
from meeting_scribe.meetings.infrastructure.filesystem_meeting_repository import (
    FilesystemMeetingRepository,
)

T0 = datetime(2026, 10, 9, 10, 15, tzinfo=timezone(timedelta(hours=2)))


def _meeting(root: Path, meeting_id: str = "2026-10-09_101500", at: datetime = T0) -> Meeting:
    (root / meeting_id).mkdir(parents=True, exist_ok=True)
    recording = RecordingInfo(started_at=at, stopped_at=at + timedelta(minutes=5))
    return Meeting.enqueue(meeting_id, root / meeting_id, recording, at)


def _write(root: Path, meeting_id: str, content: str) -> None:
    (root / meeting_id).mkdir(parents=True, exist_ok=True)
    (root / meeting_id / "meeting.json").write_text(content, encoding="utf-8")


def test_save_then_get_round_trips_the_meeting(tmp_path: Path) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    meeting = _meeting(tmp_path)
    started = meeting.start(T0, ProcessingParams(transcription={"model": "tiny"})).unwrap()

    repo.save(started).unwrap()

    assert repo.get(meeting.id).unwrap() == started


def test_saved_file_follows_schema_v1(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path)

    FilesystemMeetingRepository(tmp_path).save(meeting).unwrap()

    data = json.loads((tmp_path / meeting.id / "meeting.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["id"] == meeting.id
    assert "directory" not in data
    assert data["recording"]["started_at"] == "2026-10-09T10:15:00+02:00"
    assert data["processing"]["state"] == "QUEUED"
    assert data["processing"]["attempt"] == 1
    assert data["processing"]["failure"] is None
    assert data["speakers"] == []


def test_save_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path)

    FilesystemMeetingRepository(tmp_path).save(meeting).unwrap()

    assert sorted(p.name for p in (tmp_path / meeting.id).iterdir()) == ["meeting.json"]


def test_failed_write_keeps_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    meeting = _meeting(tmp_path)
    repo.save(meeting).unwrap()
    before = (tmp_path / meeting.id / "meeting.json").read_text(encoding="utf-8")

    def broken_replace(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    result = repo.save(meeting.start(T0, ProcessingParams()).unwrap())

    assert isinstance(result.failure(), MeetingStorageError)
    assert (tmp_path / meeting.id / "meeting.json").read_text(encoding="utf-8") == before
    assert [p.name for p in (tmp_path / meeting.id).iterdir()] == ["meeting.json"]


def test_unknown_keys_survive_a_rewrite(tmp_path: Path) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    repo.save(_meeting(tmp_path)).unwrap()
    path = tmp_path / "2026-10-09_101500" / "meeting.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_top"] = {"a": 1}
    data["processing"]["future_nested"] = "kept"
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.get("2026-10-09_101500").unwrap()
    repo.save(loaded.start(T0, ProcessingParams()).unwrap()).unwrap()

    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten["future_top"] == {"a": 1}
    assert rewritten["processing"]["future_nested"] == "kept"
    assert rewritten["processing"]["state"] == "TRANSCRIBING"


def test_get_missing_meeting_is_the_null_object(tmp_path: Path) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    (tmp_path / "no-json").mkdir()

    assert repo.get("absent").unwrap() is NULL_MEETING
    assert repo.get("no-json").unwrap() is NULL_MEETING


@pytest.mark.parametrize("meeting_id", ["", ".", "..", "../outside", "a/b"])
def test_get_never_leaves_the_meetings_directory(tmp_path: Path, meeting_id: str) -> None:
    (tmp_path / "meetings").mkdir()
    _write(tmp_path, "outside", "{}")

    assert FilesystemMeetingRepository(tmp_path / "meetings").get(meeting_id).unwrap().is_null


@pytest.mark.parametrize(
    "content", ["{not json", "[]", '{"schema_version": 1}', '{"schema_version": "1"}']
)
def test_corrupt_file_is_a_storage_failure_and_is_not_rewritten(
    tmp_path: Path, content: str
) -> None:
    _write(tmp_path, "bad", content)

    result = FilesystemMeetingRepository(tmp_path).get("bad")

    assert isinstance(result.failure(), MeetingStorageError)
    assert (tmp_path / "bad" / "meeting.json").read_text(encoding="utf-8") == content


def test_newer_schema_is_unsupported_on_get(tmp_path: Path) -> None:
    _write(tmp_path, "future", json.dumps({"schema_version": 2, "anything": True}))

    result = FilesystemMeetingRepository(tmp_path).get("future")

    assert isinstance(result.failure(), MeetingSchemaUnsupportedError)


def test_unsupported_meetings_are_never_rewritten(tmp_path: Path) -> None:
    result = FilesystemMeetingRepository(tmp_path).save(
        Meeting.unsupported("future", tmp_path / "future")
    )

    assert isinstance(result.failure(), MeetingSchemaUnsupportedError)
    assert not (tmp_path / "future").exists()


def test_list_returns_readable_meetings_newest_first(tmp_path: Path) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    older = _meeting(tmp_path, "older", T0)
    newer = _meeting(tmp_path, "newer", T0 + timedelta(hours=1))
    repo.save(older).unwrap()
    repo.save(newer).unwrap()

    assert [m.id for m in repo.list_all().unwrap()] == ["newer", "older"]


def test_list_skips_folders_without_meeting_json_and_a_file_in_the_root(tmp_path: Path) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    repo.save(_meeting(tmp_path)).unwrap()
    (tmp_path / "prototype-session" / "audio").mkdir(parents=True)
    (tmp_path / "stray.txt").write_text("x", encoding="utf-8")

    assert [m.id for m in repo.list_all().unwrap()] == ["2026-10-09_101500"]


def test_list_skips_corrupt_files_with_an_error_log_and_lists_unsupported_ones(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = FilesystemMeetingRepository(tmp_path)
    repo.save(_meeting(tmp_path)).unwrap()
    _write(tmp_path, "corrupt", "{oops")
    _write(tmp_path, "future", json.dumps({"schema_version": 9}))

    with caplog.at_level(logging.ERROR):
        meetings = repo.list_all().unwrap()

    states = {m.id: m.processing.state for m in meetings}
    assert states == {"2026-10-09_101500": "QUEUED", "future": "UNSUPPORTED"}
    assert any("corrupt" in r.getMessage() for r in caplog.records)


def test_list_of_a_missing_meetings_directory_is_empty(tmp_path: Path) -> None:
    assert FilesystemMeetingRepository(tmp_path / "nope").list_all().unwrap() == []


def test_list_fails_when_the_directory_cannot_be_read(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x", encoding="utf-8")

    assert isinstance(FilesystemMeetingRepository(not_a_dir).list_all(), Failure)
