"""Processing use cases: enqueue, resume, reprocess, and the single-worker loop (spec section 1).

One thread runs ``serve_forever``; every other method may be called from any thread (the lock
guards the queue and the preemption state). Only this class writes ``meeting.json``. Never raises.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingCorruptError,
    MeetingSchemaUnsupportedError,
    MeetingStorageError,
    WorkerCancelledError,
    WorkerProtocolError,
)
from meeting_scribe.meetings.domain.meeting import (
    UNFINISHED_STATES,
    AudioInfo,
    Meeting,
    ProcessingDevice,
    ProcessingFailure,
    ProcessingParams,
    RecordingInfo,
)
from meeting_scribe.meetings.domain.ports import (
    AudioInspector,
    Clock,
    MeetingRepository,
    NullProcessingEvents,
    ProcessingEvents,
    WorkerRunner,
)
from meeting_scribe.meetings.domain.retry_delays import check_retry_delays
from meeting_scribe.meetings.domain.timeline import Timeline
from meeting_scribe.meetings.domain.worker import (
    ProgressOrNotice,
    ResultEvent,
    StageEvent,
    StartedEvent,
    WorkerJob,
)

logger = logging.getLogger(__name__)


class JobOutcome(StrEnum):
    """How one run of a job ended; the loop acts on it (never a bare bool)."""

    DONE = "done"
    FAILED = "failed"  # the failure is recorded on disk
    INTERRUPTED = "interrupted"  # preempted or shut down; resumable
    UNSAVED = "unsaved"  # the repository refused a state change; disk still holds the old state
    UNLOADED = "unloaded"  # meeting.json could not be read (maybe transient); nothing was changed
    UNREADABLE = "unreadable"  # meeting.json is corrupt or from a newer schema; never retried
    SKIPPED = "skipped"  # nothing to do (gone, or no longer unfinished)


class ProcessingConfig(BaseModel):
    """What the service decides for every job; the worker reads no settings (spec 6.2).

    No defaults: every field is required. ``ProcessingSettings`` is the single source of the
    tuning values (and ``ProcessingParams`` of the model parameters); the composition root copies
    them here.
    """

    model_config = ConfigDict(frozen=True)

    device: Literal["cuda", "cpu"]
    worker_timeout_factor: float = Field(gt=0)
    worker_min_timeout_seconds: float = Field(gt=0)
    params: ProcessingParams
    storage_retry_delays_seconds: tuple[float, ...]  # one retry per delay, so len + 1 runs at most

    @field_validator("storage_retry_delays_seconds")
    @classmethod
    def _delays_are_valid(cls, delays: tuple[float, ...]) -> tuple[float, ...]:
        return check_retry_delays(delays)

    def stage_timeout(self, audio_seconds: float) -> float:
        return max(self.worker_min_timeout_seconds, audio_seconds * self.worker_timeout_factor)


class ProcessingStatus(BaseModel):
    """What a client is told about the pipeline right now (``Meetings1`` properties)."""

    model_config = ConfigDict(frozen=True)

    active_meeting_id: str  # empty when idle
    active_state: str  # "IDLE" or the active job's state
    queued_count: int  # jobs waiting, the active one excluded


class ProcessingController:
    def __init__(
        self,
        repository: MeetingRepository,
        runner: WorkerRunner,
        audio: AudioInspector,
        clock: Clock,
        meetings_dir: Path,
        config: ProcessingConfig,
        events: ProcessingEvents | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._audio = audio
        self._clock = clock
        self._meetings_dir = meetings_dir
        self._config = config
        self._events: ProcessingEvents = events or NullProcessingEvents()
        self._condition = threading.Condition()
        self._queue: deque[str] = deque()
        self._recording_active = False
        self._stopping = False
        self._cancel = threading.Event()  # belongs to the running job; replaced per job
        self._monotonic = monotonic
        self._storage_runs: dict[str, int] = {}  # consecutive runs ended by a storage failure
        self._delayed: list[tuple[float, str]] = []  # (due time, meeting id) waiting to retry
        self._active_id = ""  # the job the loop is running, "" when idle (guarded by the lock)
        self._active_state = "QUEUED"

    # -- commands (any thread) ------------------------------------------------------------

    def enqueue(
        self, meeting_id: str, recording: RecordingInfo
    ) -> Result[Meeting, MeetingScribeError]:
        """Queue a stopped (or crashed, OQ-1) recording for processing.

        Three cases, by what is on disk: nothing -> create ``meeting.json`` QUEUED from
        ``recording``; an unfinished meeting -> queue it as it is; a ``DONE`` or ``FAILED``
        meeting -> **reprocess** it (``recording`` is ignored, the attempt is bumped).
        """
        existing = self._repository.get(meeting_id)
        if isinstance(existing, Failure):
            return existing
        meeting = existing.unwrap()
        if meeting.is_null:
            fresh = Meeting.enqueue(
                meeting_id, self._meetings_dir / meeting_id, recording, self._clock.now()
            )
            return self._persist(fresh).map(self._push)
        if meeting.processing.state in UNFINISHED_STATES:
            return Success(self._push(meeting))
        return self.reprocess(meeting_id)

    def resume(self) -> Result[int, MeetingStorageError]:
        """Re-queue QUEUED and interrupted meetings in ``queued_at`` order (service start)."""
        listed = self._repository.list_all()
        if isinstance(listed, Failure):
            return listed
        pending = sorted(
            (m for m in listed.unwrap() if m.processing.state in UNFINISHED_STATES),
            key=lambda m: (m.processing.queued_at or m.recording.started_at, m.id),
        )
        for meeting in pending:
            self._push(meeting)
        return Success(len(pending))

    def reprocess(self, meeting_id: str) -> Result[Meeting, MeetingScribeError]:
        loaded = self._repository.get(meeting_id)
        if isinstance(loaded, Failure):
            return loaded
        found = loaded.unwrap().as_result(
            EntityNotFoundError(f"Meeting '{meeting_id}' does not exist.", detail_code="MEETING")
        )
        if isinstance(found, Failure):
            return found
        return self._commit(found.unwrap().reprocess(self._clock.now())).map(self._push)

    def get_meeting(self, meeting_id: str) -> Result[Meeting, MeetingScribeError]:
        loaded = self._repository.get(meeting_id)
        if isinstance(loaded, Failure):
            return loaded
        return loaded.unwrap().as_result(
            EntityNotFoundError(f"Meeting '{meeting_id}' does not exist.", detail_code="MEETING")
        )

    def list_meetings(self) -> Result[list[Meeting], MeetingStorageError]:
        """Every readable meeting, newest first."""
        return self._repository.list_all()

    def status(self) -> ProcessingStatus:
        with self._condition:
            return ProcessingStatus(
                active_meeting_id=self._active_id,
                active_state=self._active_state if self._active_id else "IDLE",
                queued_count=len(self._queue) + len(self._delayed),
            )

    def preempt(self) -> None:
        """A recording started: stop the running worker; its job returns to the queue head."""
        with self._condition:
            self._recording_active = True
            self._cancel.set()

    def release(self) -> None:
        """The recording ended: GPU work may start again."""
        with self._condition:
            self._recording_active = False
            self._condition.notify_all()

    def shutdown(self) -> None:
        """Stop the worker and end ``serve_forever``; the job stays resumable on disk."""
        with self._condition:
            self._stopping = True
            self._cancel.set()
            self._condition.notify_all()

    def queued_ids(self) -> tuple[str, ...]:
        """Every job the controller still owes a run, each id once.

        The ready queue in run order, then the jobs parked for a storage retry (in the order they
        were parked, not by due time). The result mixes runnable and waiting jobs: it is a census
        for a UI, not the execution order.
        """
        with self._condition:
            return (*self._queue, *(meeting_id for _, meeting_id in self._delayed))

    # -- the loop (one thread) ------------------------------------------------------------

    def serve_forever(self) -> None:
        """Run jobs one at a time, first in first out, until ``shutdown``."""
        while True:
            with self._condition:
                while not self._stopping:
                    self._promote_due_retries()
                    if self._can_start():
                        break
                    self._condition.wait(self._seconds_until_next_retry())
                if self._stopping:
                    return
            self.run_next()

    def run_next(self) -> bool:
        """Run the job at the queue head to its end; False when nothing may start now."""
        with self._condition:
            self._promote_due_retries()
            if self._stopping or not self._can_start():
                return False
            meeting_id = self._queue.popleft()
            self._cancel = cancel = threading.Event()
            self._active_id, self._active_state = meeting_id, "QUEUED"
        try:
            outcome = self._run_job(meeting_id, cancel)
        except Exception:
            logger.exception("Processing of meeting '%s' crashed unexpectedly.", meeting_id)
            self._fail_after_crash(meeting_id)
            self._clear_active()
            self._notify_status()  # back to idle: clients refresh
            return True
        self._clear_active()
        self._settle(meeting_id, outcome)
        self._notify_status()
        return True

    def _fail_after_crash(self, meeting_id: str) -> None:
        """Record an unexpected crash as a FAILED job (fixed message), so it is visible and
        can be reprocessed instead of staying "in progress" until the next service start."""
        try:
            loaded = self._repository.get(meeting_id)
            if (
                isinstance(loaded, Success)
                and loaded.unwrap().processing.state in UNFINISHED_STATES
            ):
                self._fail(loaded.unwrap(), MeetingScribeError("Processing stopped unexpectedly."))
        except Exception:
            logger.exception("Could not record the crash of meeting '%s'.", meeting_id)

    def _clear_active(self) -> None:
        with self._condition:
            self._active_id = ""

    # -- internals ------------------------------------------------------------------------

    def _can_start(self) -> bool:
        """Caller holds the lock. Pure: a job is ready and no recording holds the GPU."""
        return bool(self._queue) and not self._recording_active

    def _promote_due_retries(self) -> None:
        """Caller holds the lock. Retries whose delay has passed join the queue tail, once each."""
        now = self._monotonic()
        due = [meeting_id for at, meeting_id in self._delayed if at <= now]
        self._delayed = [(at, meeting_id) for at, meeting_id in self._delayed if at > now]
        for meeting_id in due:
            if meeting_id not in self._queue:
                self._queue.append(meeting_id)

    def _seconds_until_next_retry(self) -> float | None:
        """Caller holds the lock. How long the loop may sleep; ``None`` means until notified."""
        if not self._delayed:
            return None
        return max(0.0, min(at for at, _ in self._delayed) - self._monotonic())

    def _push(self, meeting: Meeting) -> Meeting:
        with self._condition:
            self._delayed = [(at, mid) for at, mid in self._delayed if mid != meeting.id]
            if meeting.id not in self._queue:
                self._queue.append(meeting.id)
            self._condition.notify_all()
        self._notify_status()
        return meeting

    def _persist(
        self, meeting: Meeting
    ) -> Result[Meeting, MeetingStorageError | MeetingSchemaUnsupportedError]:
        """Save, then signal: a client never sees a state that is not on disk."""
        saved = self._repository.save(meeting)
        if isinstance(saved, Success):
            with self._condition:
                if meeting.id == self._active_id:
                    self._active_state = meeting.processing.state
            self._notify(self._events.state_changed, meeting)
            self._notify_status()
        return saved

    def _commit(
        self, transition: Result[Meeting, MeetingScribeError]
    ) -> Result[Meeting, MeetingScribeError]:
        if isinstance(transition, Failure):
            return transition
        return self._persist(transition.unwrap())

    def _notify(self, callback: Callable[[Meeting], None], meeting: Meeting) -> None:
        try:
            callback(meeting)
        except Exception:
            logger.warning(
                "Processing observer failed for meeting '%s'.", meeting.id, exc_info=True
            )

    def _run_job(self, meeting_id: str, cancel: threading.Event) -> JobOutcome:
        loaded = self._repository.get(meeting_id)
        if isinstance(loaded, Failure):
            error = loaded.failure()
            if isinstance(error, MeetingSchemaUnsupportedError | MeetingCorruptError):
                logger.error(
                    "Meeting '%s' cannot be read and is skipped (%s).", meeting_id, error.error_code
                )
                self._notify_storage_failed(meeting_id, error.error_code)
                return JobOutcome.UNREADABLE  # retrying cannot change a damaged or newer file
            logger.warning("Cannot load meeting '%s' (%s).", meeting_id, error.error_code)
            return JobOutcome.UNLOADED
        if loaded.unwrap().processing.state not in UNFINISHED_STATES:
            logger.error("Meeting '%s' cannot be processed; skipped.", meeting_id)
            return JobOutcome.SKIPPED
        meeting = loaded.unwrap()
        timeline = self._audio.inspect(meeting.directory)
        if isinstance(timeline, Failure):
            recorded = self._fail(meeting, timeline.failure())
            return JobOutcome.FAILED if recorded else JobOutcome.UNSAVED
        meeting = meeting.with_audio(_audio_info(timeline.unwrap()))
        now = self._clock.now()
        transition = (
            meeting.start(now, self._config.params)
            if meeting.processing.state == "QUEUED"
            else meeting.resume(now, self._config.params)
        )
        started = self._commit(transition)
        if isinstance(started, Failure):
            logger.error("Cannot start meeting '%s': %s", meeting_id, started.failure())
            return JobOutcome.UNSAVED
        job = _Job(
            started.unwrap(),
            runner=self._runner,
            config=self._config,
            clock=self._clock,
            audio_seconds=timeline.unwrap().duration_seconds,
            cancel=cancel,
            persist=self._persist,
            save_silently=self._repository.save,
            fail=self._fail,
            finished=lambda m: self._notify(self._events.finished, m),
        )
        return job.run()

    def _settle(self, meeting_id: str, outcome: JobOutcome) -> None:
        """Act on how a job ended: requeue what is resumable, back off and bound storage trouble."""
        storage_trouble = outcome in (JobOutcome.UNSAVED, JobOutcome.UNLOADED)
        if not storage_trouble:
            self._storage_runs.pop(meeting_id, None)
        if outcome is JobOutcome.INTERRUPTED:
            self._requeue(meeting_id, head=True)  # preemption: back at the head
        elif storage_trouble:
            what = "saved" if outcome is JobOutcome.UNSAVED else "read"
            runs = self._storage_runs.get(meeting_id, 0) + 1
            delays = self._config.storage_retry_delays_seconds
            if runs > len(delays):  # one retry per delay: len(delays) + 1 runs at most
                self._storage_runs.pop(meeting_id, None)
                logger.error(
                    "Meeting '%s' could not be %s in %d runs; giving up. It stays resumable on "
                    "disk and runs again at the next resume.",
                    meeting_id,
                    what,
                    runs,
                )
                self._notify_storage_failed(meeting_id, MeetingStorageError.error_code)
                return
            self._storage_runs[meeting_id] = runs
            logger.warning(
                "Meeting '%s' could not be %s (run %d); retrying in %.0f s.",
                meeting_id,
                what,
                runs,
                delays[runs - 1],
            )
            self._delay(meeting_id, delays[runs - 1])

    def _delay(self, meeting_id: str, seconds: float) -> None:
        """Park the job until its retry is due; the queue keeps serving other jobs meanwhile."""
        with self._condition:
            if self._stopping:
                return
            if meeting_id in self._queue or any(mid == meeting_id for _, mid in self._delayed):
                return  # already scheduled (enqueued again mid-run): one entry, one extra run
            self._delayed.append((self._monotonic() + seconds, meeting_id))
            self._condition.notify_all()

    def _notify_status(self) -> None:
        try:
            self._events.status_changed()
        except Exception:
            logger.warning("Processing status observer failed.", exc_info=True)

    def _notify_storage_failed(self, meeting_id: str, error_code: str) -> None:
        try:
            self._events.storage_failed(meeting_id, error_code)
        except Exception:
            logger.warning(
                "Processing observer failed for meeting '%s'.", meeting_id, exc_info=True
            )

    def _requeue(self, meeting_id: str, *, head: bool) -> None:
        """Shutdown leaves the job on disk for the next start instead."""
        with self._condition:
            if self._stopping:
                return
            if head:
                self._queue.appendleft(meeting_id)
            else:
                self._queue.append(meeting_id)
            self._condition.notify_all()

    def _fail(self, meeting: Meeting, error: MeetingScribeError) -> bool:
        """Record the failure; False when even that could not be saved."""
        failure = ProcessingFailure(
            stage=meeting.processing.state, error_code=error.error_code, message=str(error)
        )
        logger.warning(
            "Meeting '%s' failed in %s: %s", meeting.id, failure.stage, failure.error_code
        )
        failed = self._commit(meeting.fail(failure, self._clock.now()))
        if isinstance(failed, Success):
            self._notify(self._events.finished, failed.unwrap())
            return True
        logger.error("Cannot record the failure of meeting '%s': %s", meeting.id, failed.failure())
        return False


class _Job:
    """The two worker stages of one meeting; ``meeting`` always mirrors what is on disk."""

    def __init__(
        self,
        meeting: Meeting,
        *,
        runner: WorkerRunner,
        config: ProcessingConfig,
        clock: Clock,
        audio_seconds: float,
        cancel: threading.Event,
        persist: Callable[[Meeting], Result[Meeting, MeetingScribeError]],
        save_silently: Callable[[Meeting], Result[Meeting, MeetingScribeError]],
        fail: Callable[[Meeting, MeetingScribeError], bool],
        finished: Callable[[Meeting], None],
    ) -> None:
        self.meeting = meeting
        self._runner = runner
        self._config = config
        self._clock = clock
        self._timeout = config.stage_timeout(audio_seconds)
        self._cancel = cancel
        self._persist = persist
        self._save_silently = save_silently
        self._fail = fail
        self._finished = finished

    def run(self) -> JobOutcome:
        for stage in ("transcribe", "diarize"):
            outcome = self._stage(stage)
            if isinstance(outcome, Failure):
                error = outcome.failure()
                if isinstance(error, WorkerCancelledError):
                    return JobOutcome.INTERRUPTED
                return self._failed(error)
            if (ended := self._apply_result(stage, outcome.unwrap())) is not None:
                return ended
        return JobOutcome.DONE

    def _failed(self, error: MeetingScribeError) -> JobOutcome:
        recorded = self._fail(self.meeting, error)
        return JobOutcome.FAILED if recorded else JobOutcome.UNSAVED

    def _stage(
        self, stage: Literal["transcribe", "diarize"]
    ) -> Result[ResultEvent, MeetingScribeError]:
        job = WorkerJob.build(
            stage, str(self.meeting.directory), self._config.device, self._config.params
        )
        return self._runner.run(job, self._timeout, self._on_event, self._cancel)

    def _on_event(self, event: ProgressOrNotice) -> Result[None, MeetingScribeError]:
        match event:
            case StartedEvent(device=device):
                self._record_device(device)
                return Success(None)
            case StageEvent():
                if self.meeting.processing.state != "DIARIZING":
                    return Failure(
                        WorkerProtocolError(
                            f"Unexpected stage event in {self.meeting.processing.state}."
                        )
                    )
                return self._move(self.meeting.diarization_done())
            case _:
                return Success(None)

    def _record_device(self, device: str) -> None:
        current = self.meeting.processing.device
        diarizing = self.meeting.processing.state != "TRANSCRIBING"
        updated = self.meeting.with_device(
            ProcessingDevice(
                transcription=current.transcription if diarizing else device,
                diarization=device if diarizing else current.diarization,
            )
        )
        if isinstance(self._save_silently(updated), Success):  # same state: no signal
            self.meeting = updated

    def _apply_result(self, stage: str, result: ResultEvent) -> JobOutcome | None:
        """Persist what the stage produced; ``None`` means the job goes on."""
        if stage == "transcribe":
            moved = self._move(self.meeting.transcription_done())
            return self._failed(moved.failure()) if isinstance(moved, Failure) else None
        if self.meeting.processing.state != "EXTRACTING_SAMPLES":
            return self._failed(
                WorkerProtocolError("The diarize worker ended without its stage event.")
            )
        done = self.meeting.complete(
            self._clock.now(), result.speakers, result.artifacts, result.detected_language
        )
        moved = self._move(done)
        if isinstance(moved, Failure):
            return self._failed(moved.failure())
        self._finished(self.meeting)
        return None

    def _move(
        self, transition: Result[Meeting, MeetingScribeError]
    ) -> Result[None, MeetingScribeError]:
        saved = transition.bind(self._persist)
        if isinstance(saved, Failure):
            logger.error(
                "Meeting '%s': cannot persist the transition: %s", self.meeting.id, saved.failure()
            )
            return saved
        self.meeting = saved.unwrap()
        return Success(None)


def _audio_info(timeline: Timeline) -> AudioInfo:
    return AudioInfo(
        chunk_count=len(timeline.chunks),
        total_frames=timeline.total_frames,
        duration_seconds=timeline.duration_seconds,
    )
