"""Ports implemented by meetings infrastructure adapters."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, Protocol

from returns.result import Result

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    NotificationError,
)
from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.notification import Notification
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


class MeetingLock(Protocol):
    """The lock that serialises every read-modify-write of ``meeting.json``.

    The processing controller owns it (its loop saves the same file); the review controller takes
    it through this port instead of sharing the controller object.
    """

    def hold(self) -> AbstractContextManager[object]:
        """Block until the lock is free; held for the ``with`` block, re-entrant per thread."""
        ...


type GpuHolder = Literal["recording", "processing", "summary"]


class GpuLease(Protocol):
    """Who may use the single GPU (ADR-003, ADR-007). Thread-safe; never blocks.

    A ``recording`` is always granted and revokes every other holder (their ``on_revoke`` is
    called); a ``processing`` worker or a local ``summary`` is granted only when nobody holds it.
    """

    def available(self, holder: GpuHolder) -> bool:
        """Whether ``acquire`` would succeed now (the answer can change at once)."""
        ...

    def acquire(self, holder: GpuHolder, on_revoke: Callable[[], None] | None = None) -> bool:
        """Take the lease; ``on_revoke`` runs, once, if a recording starts while it is held."""
        ...

    def release(self, holder: GpuHolder) -> None:
        """Give it back; a holder that does not hold it is ignored."""
        ...


class SampleReader(Protocol):
    """Reads a speaker's voice sample so clients get bytes, never a path."""

    def read(self, meeting_dir: Path, relative_path: str) -> Result[bytes, MeetingScribeError]:
        """The file at ``relative_path`` inside the meeting folder.

        ``ENTITY_NOT_FOUND_SPEAKER_SAMPLE`` when it is missing, unreadable, larger than the sample
        bound or outside the folder. Never raises and never quotes a path in an error.
        """
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

    def status_changed(self) -> None:
        """The active job or the queue length changed; read the controller's ``status()``.

        Carries no data on purpose: the observer reads the freshest status itself, so callbacks
        that arrive out of order can never publish a stale one.
        """
        ...


class NullProcessingEvents:
    def state_changed(self, meeting: Meeting) -> None:  # noqa: ARG002
        return None

    def finished(self, meeting: Meeting) -> None:  # noqa: ARG002
        return None

    def storage_failed(self, meeting_id: str, error_code: str) -> None:  # noqa: ARG002
        return None

    def status_changed(self) -> None:
        return None


class AudioMixReader(Protocol):
    def read_mix(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[Samples, MeetingStorageError]:
        """The session as one mono float32 signal, ``(ch0 + ch1) / 2`` per frame (spec 2.2)."""
        ...


class ChannelEnergies(NamedTuple):
    """Mean square per 100 ms frame of each channel of the session (spec 2.3, 2.4)."""

    mic: list[float]
    system: list[float]


class AudioChannelReader(Protocol):
    def read_energies(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[ChannelEnergies, MeetingStorageError]:
        """Both frame-energy envelopes, decoded one channel at a time (no whole stereo signal)."""
        ...

    def read_channel(
        self, meeting_dir: Path, timeline: Timeline, channel: Literal["mic", "system"]
    ) -> Result[Samples, MeetingStorageError]:
        """One channel as a mono float32 signal in [-1, 1) (``mic`` is 0, ``system`` is 1)."""
        ...


class Notifier(Protocol):
    def notify(self, notification: Notification) -> Result[None, NotificationError]:
        """Show a desktop notification. Blocks briefly; never raises."""
        ...
