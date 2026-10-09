"""``meeting.json`` persistence: one folder per meeting, atomic writes (ADR-004)."""

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.meetings.domain.errors import MeetingSchemaUnsupportedError, MeetingStorageError
from meeting_scribe.meetings.domain.meeting import NULL_MEETING, SCHEMA_VERSION, Meeting

logger = logging.getLogger(__name__)

_FILE_NAME = "meeting.json"


class FilesystemMeetingRepository:
    def __init__(self, meetings_dir: Path) -> None:
        self._root = meetings_dir

    def get(
        self, meeting_id: str
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        if not self._is_folder_name(meeting_id):
            return Success(NULL_MEETING)
        path = self._root / meeting_id / _FILE_NAME
        if not path.is_file():
            return Success(NULL_MEETING)
        return self._read(meeting_id, path)

    def save(
        self, meeting: Meeting
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        if meeting.processing.state == "UNSUPPORTED" or meeting.is_null:
            return Failure(
                MeetingSchemaUnsupportedError(f"Meeting '{meeting.id}' is never rewritten.")
            )
        if not self._is_folder_name(meeting.id) or meeting.directory != self._root / meeting.id:
            return Failure(
                MeetingStorageError(
                    f"Meeting '{meeting.id}' must live in '{self._root / meeting.id}', "
                    f"not '{meeting.directory}'."
                )
            )
        text = json.dumps(meeting.model_dump(mode="json"), indent=2, ensure_ascii=False)
        try:
            self._write_atomically(meeting.directory / _FILE_NAME, text)
        except OSError as error:
            return Failure(
                MeetingStorageError(
                    f"Cannot write meeting '{meeting.id}': {error.strerror or error}"
                )
            )
        return Success(meeting)

    def list_all(self) -> Result[list[Meeting], MeetingStorageError]:
        try:
            folders = (
                sorted(p for p in self._root.iterdir() if p.is_dir()) if self._root.exists() else []
            )
        except OSError as error:
            return Failure(MeetingStorageError(f"Cannot list meetings: {error.strerror or error}"))
        meetings: list[Meeting] = []
        for folder in folders:
            path = folder / _FILE_NAME
            if not path.is_file():
                continue  # prototype sessions and crashed recordings are ignored (OQ-2)
            read = self._read(folder.name, path)
            match read:
                case Success(meeting):
                    meetings.append(meeting)
                case Failure(MeetingSchemaUnsupportedError()):
                    meetings.append(Meeting.unsupported(folder.name, folder))
                case Failure(error):
                    logger.error("Skipping meeting '%s': %s", folder.name, error)
        return Success(sorted(meetings, key=lambda m: m.recording.started_at, reverse=True))

    def _read(
        self, meeting_id: str, path: Path
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return Failure(MeetingStorageError(f"Cannot read meeting '{meeting_id}': {error}"))
        version = data.get("schema_version") if isinstance(data, dict) else None
        if type(version) is not int or version < 1:
            return Failure(
                MeetingStorageError(f"Meeting '{meeting_id}' has a corrupt {_FILE_NAME}.")
            )
        if version > SCHEMA_VERSION:
            return Failure(
                MeetingSchemaUnsupportedError(
                    f"Meeting '{meeting_id}' uses schema {version}; supported: {SCHEMA_VERSION}."
                )
            )
        if data.get("id") != meeting_id:
            return Failure(
                MeetingStorageError(f"Meeting '{meeting_id}' has a corrupt {_FILE_NAME}.")
            )
        try:
            return Success(Meeting.model_validate({**data, "directory": path.parent}))
        except ValidationError as error:
            return Failure(
                MeetingStorageError(f"Meeting '{meeting_id}' has an invalid {_FILE_NAME}: {error}")
            )

    def _is_folder_name(self, meeting_id: str) -> bool:
        return meeting_id not in ("", ".", "..") and Path(meeting_id).name == meeting_id

    def _write_atomically(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{_FILE_NAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temp_name).replace(target)  # os.replace: atomic on POSIX
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        self._fsync_directory(target.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Make the rename itself durable: without it a power loss can undo the new state.

        The new file is already visible, so a failure here (some FUSE/CIFS mounts reject fsync on
        a directory) is logged, not reported: the save did land (GO-032).
        """
        try:
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as error:
            logger.warning("Saved in '%s' but the rename may not be durable: %s", directory, error)
