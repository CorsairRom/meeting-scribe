"""Ports implemented by meetings infrastructure adapters."""

from typing import Protocol

from returns.result import Result

from meeting_scribe.meetings.domain.errors import MeetingSchemaUnsupportedError, MeetingStorageError
from meeting_scribe.meetings.domain.meeting import Meeting


class MeetingRepository(Protocol):
    def get(
        self, meeting_id: str
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        """``Success(NULL_MEETING)`` when the meeting has no ``meeting.json``; never ``None``."""
        ...

    def save(
        self, meeting: Meeting
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        """Persist atomically. An ``UNSUPPORTED`` meeting is never rewritten."""
        ...

    def list_all(self) -> Result[list[Meeting], MeetingStorageError]:
        """Meetings with a readable ``meeting.json``, newest first. Unreadable ones are skipped."""
        ...
