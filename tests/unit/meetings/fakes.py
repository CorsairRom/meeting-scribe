"""In-memory fakes for the meetings ports (CR-009): no files, no processes, no models."""

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    WorkerCancelledError,
)
from meeting_scribe.meetings.domain.meeting import (
    NULL_MEETING,
    Meeting,
    RecordingInfo,
    SpeakerEntry,
)
from meeting_scribe.meetings.domain.ports import CancelSignal
from meeting_scribe.meetings.domain.timeline import ChunkHeader, Timeline, build_timeline
from meeting_scribe.meetings.domain.worker import (
    ProgressOrNotice,
    ResultEvent,
    StageEvent,
    StartedEvent,
    WorkerJob,
)

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

    def state_changed(self, meeting: Meeting) -> None:
        stored = self._repository.meetings[meeting.id].processing.state
        self.state_changes.append((meeting.id, meeting.processing.state, stored))

    def finished(self, meeting: Meeting) -> None:
        self.finished_ids.append(meeting.id)

    def storage_failed(self, meeting_id: str, error_code: str) -> None:
        self.storage_failures.append((meeting_id, error_code))


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
