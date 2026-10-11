import os
from pathlib import Path

import pytest
from returns.result import Failure

from meeting_scribe.meetings.infrastructure.filesystem_summary_files import FilesystemSummaryFiles

NEW = "# Synthetic summary two\n"
OLD = "# Synthetic summary one\n"


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_save_writes_summary_md_and_returns_its_name(tmp_path: Path) -> None:
    files = FilesystemSummaryFiles()

    assert files.save(tmp_path, OLD).unwrap() == "summary.md"

    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == OLD
    assert _names(tmp_path) == ["summary.md"]  # no temp file, no backup yet


def test_regenerating_keeps_exactly_one_backup_replaced_each_time(tmp_path: Path) -> None:
    files = FilesystemSummaryFiles()
    files.save(tmp_path, OLD).unwrap()

    files.save(tmp_path, NEW).unwrap()
    assert (tmp_path / "summary.previous.md").read_text(encoding="utf-8") == OLD
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == NEW

    files.save(tmp_path, "# Synthetic summary three\n").unwrap()
    assert (tmp_path / "summary.previous.md").read_text(encoding="utf-8") == NEW
    assert _names(tmp_path) == ["summary.md", "summary.previous.md"]


def test_a_failed_write_leaves_the_current_summary_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = FilesystemSummaryFiles()
    files.save(tmp_path, OLD).unwrap()
    real_replace = os.replace

    def failing(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "summary.md":
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing)

    result = files.save(tmp_path, NEW)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_STORAGE_FAILED"
    assert str(tmp_path) not in str(result.failure())
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == OLD
    assert not [name for name in _names(tmp_path) if name.endswith(".tmp")]


def test_a_missing_folder_is_a_storage_failure(tmp_path: Path) -> None:
    result = FilesystemSummaryFiles().save(tmp_path / "gone", OLD)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_STORAGE_FAILED"


def test_read_returns_the_text_and_a_missing_file_is_entity_not_found(tmp_path: Path) -> None:
    files = FilesystemSummaryFiles()
    missing = files.read(tmp_path)
    assert isinstance(missing, Failure)
    assert missing.failure().error_code == "ENTITY_NOT_FOUND_SUMMARY"

    files.save(tmp_path, OLD).unwrap()

    assert files.read(tmp_path).unwrap() == OLD


def test_read_of_an_unreadable_file_is_a_storage_failure(tmp_path: Path) -> None:
    (tmp_path / "summary.md").write_bytes(b"\xff\xfe")

    result = FilesystemSummaryFiles().read(tmp_path)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_STORAGE_FAILED"
