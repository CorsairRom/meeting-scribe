"""The meetings-folder check (AC-7): absolute, creatable, writable; nothing is moved or listed."""

from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.preferences.infrastructure.meetings_folder import check_meetings_folder


def test_an_existing_writable_folder_is_accepted(tmp_path: Path) -> None:
    assert check_meetings_folder(str(tmp_path)) == Success(tmp_path)


def test_a_missing_folder_with_an_existing_parent_is_created_private(tmp_path: Path) -> None:
    target = tmp_path / "new-meetings"

    assert check_meetings_folder(str(target)) == Success(target)

    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700


def test_the_probe_leaves_nothing_behind(tmp_path: Path) -> None:
    check_meetings_folder(str(tmp_path)).unwrap()

    assert list(tmp_path.iterdir()) == []


def test_tilde_is_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert check_meetings_folder("~/meetings") == Success(tmp_path / "meetings")


@pytest.mark.parametrize("raw", ["relative/path", "", "../escape", "/a/../b"])
def test_a_relative_or_dotdot_path_is_invalid(raw: str) -> None:
    result = check_meetings_folder(raw)

    assert isinstance(result, Failure)
    assert result.failure().code == "FIELD_INVALID"
    assert result.failure().key == "storage.meetings_dir"


def test_a_file_is_invalid(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")

    result = check_meetings_folder(str(file))

    assert isinstance(result, Failure)
    assert result.failure().code == "FIELD_INVALID"


def test_a_folder_whose_parent_is_missing_is_not_creatable(tmp_path: Path) -> None:
    result = check_meetings_folder(str(tmp_path / "a" / "b"))

    assert isinstance(result, Failure)
    assert result.failure().code == "FIELD_FOLDER_NOT_WRITABLE"


def test_a_read_only_folder_is_not_writable_and_the_message_hides_the_path(
    tmp_path: Path,
) -> None:
    locked = tmp_path / "secret-name"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = check_meetings_folder(str(locked))
    finally:
        locked.chmod(0o700)

    assert isinstance(result, Failure)
    assert result.failure().code == "FIELD_FOLDER_NOT_WRITABLE"
    assert "secret-name" not in result.failure().message


def test_existing_content_is_never_touched(tmp_path: Path) -> None:
    (tmp_path / "m1").mkdir()
    (tmp_path / "m1" / "meeting.json").write_text("{}", encoding="utf-8")

    check_meetings_folder(str(tmp_path)).unwrap()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["m1"]
