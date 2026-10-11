"""``summary.md`` on disk: an atomic write that keeps one backup (spec 7, OQ-5).

The file name is fixed, never taken from ``meeting.json``, so a damaged or edited section cannot
point a read outside the meeting folder. Errors never quote a path.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import SummaryStorageError

logger = logging.getLogger(__name__)

SUMMARY_FILE = "summary.md"
PREVIOUS_FILE = "summary.previous.md"
_PREFIX = ".summary."


class FilesystemSummaryFiles:
    def save(self, meeting_dir: Path, markdown: str) -> Result[str, SummaryStorageError]:
        """Write a temp file, back the current one up, then replace; a failure restores both."""
        temps: list[str] = []
        previous = meeting_dir / PREVIOUS_FILE
        held: str | None = None
        try:
            new_temp = self._temp(meeting_dir)
            temps.append(new_temp)
            with Path(new_temp).open("w", encoding="utf-8") as handle:
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
            current = meeting_dir / SUMMARY_FILE
            backup_temp = ""
            if current.is_file():
                backup_temp = self._temp(meeting_dir)
                temps.append(backup_temp)
                shutil.copyfile(current, backup_temp)
                if previous.is_file():  # keep the older backup until the new file is in place
                    held = self._temp(meeting_dir)
                    temps.append(held)
                    previous.replace(held)
            try:
                if current.is_file():
                    Path(backup_temp).replace(previous)
                Path(new_temp).replace(current)
            except OSError:
                if held is not None:  # both files survive every failure path
                    Path(held).replace(previous)
                raise
        except OSError as error:
            logger.warning("Cannot write the summary: %s", error.strerror or type(error).__name__)
            return Failure(
                SummaryStorageError(
                    "The summary could not be saved. Check disk space and permissions."
                )
            )
        finally:
            for name in temps:
                Path(name).unlink(missing_ok=True)
        return Success(SUMMARY_FILE)

    def exists(self, meeting_dir: Path) -> bool:
        return (meeting_dir / SUMMARY_FILE).is_file()

    def read(self, meeting_dir: Path) -> Result[str, MeetingScribeError]:
        path = meeting_dir / SUMMARY_FILE
        if not path.is_file():
            return Failure(
                EntityNotFoundError("The meeting has no summary yet.", detail_code="SUMMARY")
            )
        try:
            return Success(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Failure(SummaryStorageError("The summary could not be read."))

    @staticmethod
    def _temp(meeting_dir: Path) -> str:
        fd, name = tempfile.mkstemp(dir=meeting_dir, prefix=_PREFIX, suffix=".tmp")
        os.close(fd)
        return name
