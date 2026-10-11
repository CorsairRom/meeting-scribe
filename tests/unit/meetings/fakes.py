"""In-memory fakes for the meetings ports (CR-009): no files, no processes, no models."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    NotificationError,
    SummaryStorageError,
    WorkerCancelledError,
)
from meeting_scribe.meetings.domain.meeting import (
    NULL_MEETING,
    Meeting,
    RecordingInfo,
    SpeakerEntry,
)
from meeting_scribe.meetings.domain.notification import Notification
from meeting_scribe.meetings.domain.ports import CancelSignal
from meeting_scribe.meetings.domain.timeline import ChunkHeader, Timeline, build_timeline
from meeting_scribe.meetings.domain.worker import (
    ProgressOrNotice,
    ResultEvent,
    StageEvent,
    StartedEvent,
    WorkerJob,
)
from meeting_scribe.speakers.application.render_transcript import Utterance
from meeting_scribe.summaries.domain.errors import SummaryError

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=timezone(timedelta(hours=2)))
ROOT = Path("/meetings")

type OnEvent = Callable[[ProgressOrNotice], Result[None, MeetingScribeError]]
type Behaviour = Callable[
    [WorkerJob, OnEvent, CancelSignal], Result[ResultEvent, MeetingScribeError]
]

RECORDING = RecordingInfo(started_at=T0, stopped_at=T0 + timedelta(minutes=5), state="STOPPED")


class InMemoryMeetingRepository:
    def __init__(self) -> None:
        self.meetings: dict[str, Meeting] = {}
        self.saves: list[tuple[str, str]] = []
        self.fail_saves = False
        self.fail_saves_for: set[str] = set()
        self.fail_on_save_number: int | None = None  # 1-based, counted over successful saves
        self.lock: FakeMeetingLock | None = None
        self.lock_held_on_save: list[bool] = []
        self.get_failures: dict[str, MeetingStorageError | MeetingSchemaUnsupportedError] = {}

    def get(
        self, meeting_id: str
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        if meeting_id in self.get_failures:
            return Failure(self.get_failures[meeting_id])
        return Success(self.meetings.get(meeting_id, NULL_MEETING))

    def save(
        self, meeting: Meeting
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        if self.fail_saves or meeting.id in self.fail_saves_for:
            return Failure(MeetingStorageError("disk full"))
        if self.fail_on_save_number == len(self.saves) + 1:
            return Failure(MeetingStorageError("disk full"))
        if self.lock is not None:
            self.lock_held_on_save.append(self.lock.held)
        self.meetings[meeting.id] = meeting
        self.saves.append((meeting.id, meeting.processing.state))
        return Success(meeting)

    def list_all(self) -> Result[list[Meeting], MeetingStorageError]:
        ordered = sorted(self.meetings.values(), key=lambda m: m.recording.started_at, reverse=True)
        return Success(ordered)


class FakeClock:
    def __init__(self) -> None:
        self.current = T0 + timedelta(minutes=10)

    def now(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


class FakeMonotonic:
    """A manually advanced monotonic clock: retry delays are tested without sleeping."""

    def __init__(self) -> None:
        self.current = 1_000.0

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class FakeAudioInspector:
    def __init__(self, frames: int = 480_640) -> None:
        self.frames = frames
        self.missing: set[str] = set()

    def inspect(self, meeting_dir: Path) -> Result[Timeline, MeetingAudioMissingError]:
        if meeting_dir.name in self.missing:
            return Failure(MeetingAudioMissingError("no chunk"))
        header = ChunkHeader(
            name="chunk_00000.wav",
            frames=self.frames,
            size_bytes=self.frames * 4 + 44,
            channels=2,
            sample_rate=16_000,
            sample_width_bytes=2,
        )
        return build_timeline([header]).alt(lambda e: MeetingAudioMissingError(str(e)))  # type: ignore[arg-type]


class RecordingEvents:
    """Records what the repository held when each signal fired (persist-before-signal)."""

    def __init__(self, repository: InMemoryMeetingRepository) -> None:
        self._repository = repository
        self.state_changes: list[tuple[str, str, str]] = []
        self.finished_ids: list[str] = []
        self.storage_failures: list[tuple[str, str]] = []
        self.status_changes = 0

    def state_changed(self, meeting: Meeting) -> None:
        stored = self._repository.meetings[meeting.id].processing.state
        self.state_changes.append((meeting.id, meeting.processing.state, stored))

    def finished(self, meeting: Meeting) -> None:
        self.finished_ids.append(meeting.id)

    def storage_failed(self, meeting_id: str, error_code: str) -> None:
        self.storage_failures.append((meeting_id, error_code))

    def status_changed(self) -> None:
        self.status_changes += 1


def speakers() -> tuple[SpeakerEntry, ...]:
    return (SpeakerEntry(id="S1", label="Speaker 1"),)


def transcribe_ok(
    job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
) -> Result[ResultEvent, MeetingScribeError]:
    on_event(StartedEvent(pid=1, device="cuda/float16"))
    return Success(ResultEvent(artifacts={"words": "words.json"}))


def diarize_ok(
    job: WorkerJob, on_event: OnEvent, cancel: CancelSignal
) -> Result[ResultEvent, MeetingScribeError]:
    on_event(StartedEvent(pid=2, device="cuda"))
    on_event(StageEvent(state="EXTRACTING_SAMPLES"))
    return Success(
        ResultEvent(
            artifacts={"transcript": "transcript.md"}, speakers=speakers(), detected_language="es"
        )
    )


class ScriptedRunner:
    """A ``WorkerRunner`` whose behaviour is chosen per (meeting id, stage)."""

    def __init__(self) -> None:
        self.behaviours: dict[tuple[str, str], Behaviour] = {}
        self.calls: list[tuple[str, str, float]] = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()

    def run(
        self, job: WorkerJob, timeout_seconds: float, on_event: OnEvent, cancel: CancelSignal
    ) -> Result[ResultEvent, MeetingScribeError]:
        meeting_id = Path(job.meeting_dir).name
        with self._lock:
            self.calls.append((meeting_id, job.stage, timeout_seconds))
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if cancel.is_set():
                return Failure(WorkerCancelledError("cancelled before start"))
            default = transcribe_ok if job.stage == "transcribe" else diarize_ok
            return self.behaviours.get((meeting_id, job.stage), default)(job, on_event, cancel)
        finally:
            with self._lock:
                self._active -= 1

    def stages_for(self, meeting_id: str) -> list[str]:
        return [stage for mid, stage, _ in self.calls if mid == meeting_id]


class FakeNotifier:
    """Stands in for ``org.freedesktop.Notifications``: tests never reach the real one."""

    def __init__(self, error: NotificationError | None = None) -> None:
        self.error = error
        self.sent: list[Notification] = []

    def notify(self, notification: Notification) -> Result[None, NotificationError]:
        self.sent.append(notification)
        return Failure(self.error) if self.error else Success(None)


class FakeMeetingLock:
    """Records whether the lock is held, so tests can prove work happens inside it."""

    def __init__(self) -> None:
        self.held = False
        self.holds = 0

    @contextmanager
    def hold(self) -> Iterator[None]:
        self.holds += 1
        self.held = True
        try:
            yield
        finally:
            self.held = False


class FakeUtterancesStore:
    """In-memory ``UtterancesStore``; notes meeting.json and the lock at each transcript write."""

    def __init__(self, repository: InMemoryMeetingRepository, lock: FakeMeetingLock) -> None:
        self._repository = repository
        self._lock = lock
        self.utterances: tuple[Utterance, ...] = ()
        self.load_failure: MeetingScribeError | None = None
        self.write_failure: MeetingScribeError | None = None
        self.transcripts: list[str] = []
        self.stale_when_written: list[bool] = []
        self.lock_held_when_written: list[bool] = []

    def load(self, meeting_dir: Path) -> Result[tuple[Utterance, ...], MeetingScribeError]:
        if self.load_failure is not None:
            return Failure(self.load_failure)
        return Success(self.utterances)

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, MeetingScribeError]:
        if self.write_failure is not None:
            return Failure(self.write_failure)
        saved = self._repository.meetings[meeting_dir.name]
        self.stale_when_written.append(saved.speaker_review.transcript_stale)
        self.lock_held_when_written.append(self._lock.held)
        self.transcripts.append(text)
        return Success("transcript.md")


class FakeSampleReader:
    """In-memory ``SampleReader``: files keyed by their path relative to the meeting folder."""

    def __init__(self, lock: FakeMeetingLock) -> None:
        self._lock = lock
        self.files: dict[str, bytes] = {}
        self.lock_held_when_read: list[bool] = []
        self.requested: list[str] = []

    def read(self, meeting_dir: Path, relative_path: str) -> Result[bytes, MeetingScribeError]:
        self.requested.append(relative_path)
        self.lock_held_when_read.append(self._lock.held)
        if relative_path not in self.files:
            return Failure(
                EntityNotFoundError("The sample is not available.", detail_code="SPEAKER_SAMPLE")
            )
        return Success(self.files[relative_path])


class FakeSummaryFiles:
    """In-memory ``SummaryFiles`` with the same one-backup rotation as the real store."""

    def __init__(self, repository: InMemoryMeetingRepository, lock: FakeMeetingLock) -> None:
        self._repository = repository
        self._lock = lock
        self.texts: dict[str, str] = {}
        self.previous: dict[str, str] = {}
        self.saves: list[str] = []
        self.fail_saves = False
        self.lock_held_on_save: list[bool] = []
        self.section_when_saved: list[bool] = []

    def save(self, meeting_dir: Path, markdown: str) -> Result[str, SummaryStorageError]:
        if self.fail_saves:
            return Failure(SummaryStorageError("disk full"))
        name = meeting_dir.name
        self.lock_held_on_save.append(self._lock.held)
        stored = self._repository.meetings.get(name)
        self.section_when_saved.append(bool(stored and stored.summary and stored.summary.file))
        if name in self.texts:
            self.previous[name] = self.texts[name]
        self.texts[name] = markdown
        self.saves.append(name)
        return Success("summary.md")

    def read(self, meeting_dir: Path) -> Result[str, MeetingScribeError]:
        if meeting_dir.name not in self.texts:
            return Failure(EntityNotFoundError("no summary", detail_code="SUMMARY"))
        return Success(self.texts[meeting_dir.name])

    def exists(self, meeting_dir: Path) -> bool:
        return meeting_dir.name in self.texts

    def path(self, meeting_dir: Path) -> Path:
        return meeting_dir / "summary.md"


class FakeKeyStore:
    """A ``CloudKeyStore`` that counts reads; the key is an obvious fake."""

    FAKE_KEY = "fake-key-not-a-secret"

    def __init__(self, failure: SummaryError | None = None) -> None:
        self.failure = failure
        self.reads = 0

    def get_key(self) -> Result[SecretStr, SummaryError]:
        self.reads += 1
        if self.failure is not None:
            return Failure(self.failure)
        return Success(SecretStr(self.FAKE_KEY))


class RecordingSummaryEvents:
    """Records every summary signal in order, with the stored state at the time."""

    def __init__(self) -> None:
        self.log: list[tuple[str, ...]] = []

    def progress(self, meeting_id: str, step: int, total: int) -> None:
        self.log.append(("progress", meeting_id, str(step), str(total)))

    def finished(self, meeting_id: str, state: str, error_code: str) -> None:
        self.log.append(("finished", meeting_id, state, error_code))

    def active_changed(self) -> None:
        self.log.append(("active",))

    def finishes(self) -> list[tuple[str, ...]]:
        return [entry for entry in self.log if entry[0] == "finished"]


class ManualStarter:
    """Collects the job instead of starting a thread: tests run it when they want to."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)

    def run_next(self) -> None:
        self.jobs.pop(0)()
