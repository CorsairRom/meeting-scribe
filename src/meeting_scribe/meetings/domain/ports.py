"""Ports implemented by meetings infrastructure adapters."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from returns.result import Result

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.timeline import Timeline
from meeting_scribe.meetings.domain.worker import ProgressOrNotice, ResultEvent, WorkerJob


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


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware current time."""
        ...


class AudioInspector(Protocol):
    def inspect(
        self, meeting_dir: Path
    ) -> Result[
        Timeline, MeetingAudioMissingError | MeetingAudioUnsupportedError | MeetingStorageError
    ]:
        """Build the timeline from the chunk headers under ``<meeting_dir>/audio``."""
        ...


class CancelSignal(Protocol):
    """Set from another thread to stop a running worker (``threading.Event`` fits)."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class WorkerRunner(Protocol):
    def run(
        self,
        job: WorkerJob,
        timeout_seconds: float,
        on_event: Callable[[ProgressOrNotice], Result[None, MeetingScribeError]],
        cancel: CancelSignal,
    ) -> Result[ResultEvent, MeetingScribeError]:
        """Run one worker to its end; blocks, so call it from a thread, never the event loop.

        ``on_event`` receives every non-final event in order; a ``Failure`` from it stops the
        worker and becomes the outcome. ``cancel`` set (before or during the run) stops the worker
        and yields ``WorkerCancelledError``. Other failures: ``WorkerTimeoutError``,
        ``WorkerCrashedError``, ``WorkerProtocolError``, ``WorkerReportedError``.
        """
        ...


class ProcessingEvents(Protocol):
    """Observer of the job lifecycle (D-Bus signals and notifications are wired on it later)."""

    def state_changed(self, meeting: Meeting) -> None:
        """Called after the new state is persisted."""
        ...

    def finished(self, meeting: Meeting) -> None:
        """Called after a job ends ``DONE`` or ``FAILED`` was persisted."""
        ...

    def storage_failed(self, meeting_id: str, error_code: str) -> None:
        """Called when a job cannot go on because ``meeting.json`` cannot be read or saved.

        Only the meeting id and a stable ``error_code``: the repository's own error text can quote
        ``meeting.json`` content, which must never leave the service.

        The meeting is left as it is on disk (resumable at the next ``resume()``); nothing in
        its persisted state says so, so this is how a UI learns that the job is stuck.
        """
        ...


class NullProcessingEvents:
    def state_changed(self, meeting: Meeting) -> None:  # noqa: ARG002
        return None

    def finished(self, meeting: Meeting) -> None:  # noqa: ARG002
        return None

    def storage_failed(self, meeting_id: str, error_code: str) -> None:  # noqa: ARG002
        return None


class AudioMixReader(Protocol):
    def read_mix(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[Samples, MeetingStorageError]:
        """The session as one mono float32 signal, ``(ch0 + ch1) / 2`` per frame (spec 2.2)."""
        ...
