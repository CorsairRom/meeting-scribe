"""The meetings-folder check (AC-7): absolute, creatable, writable; nothing is moved or listed."""

import os
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.preferences.infrastructure.meetings_folder import (
    MeetingsFolderPreparer,
    check_meetings_folder,
    create_meetings_folder,
)


def test_an_existing_writable_folder_is_accepted(tmp_path: Path) -> None:
    assert check_meetings_folder(str(tmp_path)) == Success(tmp_path)


def test_checking_a_missing_folder_with_an_existing_parent_creates_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "new-meetings"

    assert check_meetings_folder(str(target)) == Success(target)

    assert not target.exists()


def test_creating_a_missing_folder_makes_it_private(tmp_path: Path) -> None:
    target = tmp_path / "new-meetings"

    assert create_meetings_folder(str(target)) == Success(target)

    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700


def test_creating_under_a_missing_parent_is_not_writable(tmp_path: Path) -> None:
    result = create_meetings_folder(str(tmp_path / "a" / "b"))

    assert isinstance(result, Failure)
    assert result.failure().code == "FIELD_FOLDER_NOT_WRITABLE"


@pytest.mark.parametrize("raw", ["~no-such-user-xyz/meetings", "/a/\x00/meetings"])
def test_a_home_of_an_unknown_user_or_a_nul_byte_is_a_problem_not_an_exception(raw: str) -> None:
    for check in (check_meetings_folder, create_meetings_folder):
        result = check(raw)

        assert isinstance(result, Failure)
        assert result.failure().code == "FIELD_INVALID"
        assert "no-such-user" not in result.failure().message


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
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


def test_a_committed_folder_is_never_rolled_back(tmp_path: Path) -> None:
    preparer = MeetingsFolderPreparer()
    target = tmp_path / "new-meetings"
    assert preparer(str(target)) == Success(target)

    preparer.commit()
    preparer.rollback()

    assert target.is_dir()


def test_a_folder_that_was_there_is_never_removed_by_a_rollback(tmp_path: Path) -> None:
    preparer = MeetingsFolderPreparer()
    assert preparer(str(tmp_path)) == Success(tmp_path)

    preparer.rollback()

    assert tmp_path.is_dir()
