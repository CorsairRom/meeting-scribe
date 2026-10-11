"""Meeting summaries: gates, one job thread, write order and staleness (spec ``summaries.md`` 7).

The meeting lock is held only to snapshot and to save, never during the model call, so processing
and review calls are not blocked for minutes. A running summary is not persisted. A saved summary
is ``summary.md`` first, then the ``summary`` section of ``meeting.json``; a crash in between
leaves a file without a section, which reads as "no summary" and is overwritten by the next run.
Staleness is computed from the fingerprint of the current effective transcript; renames, merges
and reprocessing never touch ``summary.md``. Nothing here logs a name, a transcript line, a
summary text or the cloud key.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    InvalidValueError,
    MeetingScribeError,
)
from meeting_scribe.meetings.application.review_controller import ReviewController
from meeting_scribe.meetings.domain.errors import (
    MeetingNotProcessedError,
    SummaryAlreadyRunningError,
    SummaryBusyError,
    SummaryCloudDisabledError,
    SummaryCloudNotConfirmedError,
    SummaryGpuBusyError,
)
from meeting_scribe.meetings.domain.meeting import Meeting, SummaryInfo
from meeting_scribe.meetings.domain.ports import (
    Clock,
    GpuLease,
    MeetingLock,
    MeetingRepository,
    NullSummaryEvents,
    SummaryEvents,
    SummaryFiles,
    SummaryOutcome,
)
from meeting_scribe.summaries.application.generate_summary import (
    CloudKeyStore,
    CompletionLimits,
    GeneratedSummary,
    GenerateSummary,
    SummaryCancelledError,
    SummaryLine,
    SummaryNoSpeechError,
    SummaryRequest,
    input_fingerprint,
)

logger = logging.getLogger(__name__)

type ProviderId = Literal["local", "cloud"]
type SummaryState = Literal["NONE", "RUNNING", "DONE", "FAILED"]
type JobStarter = Callable[[Callable[[], None]], object]


@dataclass(frozen=True)
class ConfiguredProvider:
    """One provider as the composition root built it: label, model, budget and its use case."""

    id: ProviderId
    label: str
    model: str
    limits: CompletionLimits
    generate: GenerateSummary

    @property
    def cloud(self) -> bool:
        return self.id == "cloud"


@dataclass(frozen=True)
class SummaryConfig:
    local: ConfiguredProvider
    cloud: ConfiguredProvider
    cloud_enabled: bool
    default_provider: ProviderId
    language: str  # "auto" follows the meeting; anything else is an override
    total_timeout_seconds: float


@dataclass(frozen=True)
class ProviderRow:
    """One ``Providers`` row; the cloud key and URL are never part of it."""

    id: ProviderId
    label: str
    cloud: bool
    model: str
    available: bool
    reason: str
    default: bool


@dataclass(frozen=True)
class SummaryStatus:
    state: SummaryState
    stale: bool
    cloud: bool
    provider_label: str
    generated_at: datetime | None
    step: int
    total_steps: int
    failure_code: str


@dataclass(frozen=True)
class SummaryDocument:
    markdown: str
    title: str
    generated_at: datetime | None
    provider: str
    provider_label: str
    cloud: bool
    model: str
    language: str
    stale: bool


class _Job:
    """The one running summary: what to run, how to stop it and how far it got."""

    def __init__(
        self, meeting_id: str, option: ConfiguredProvider, request: SummaryRequest
    ) -> None:
        self.meeting_id = meeting_id
        self.option = option
        self.request = request
        self.cancel = threading.Event()
        self.step = 0
        self.total = 1


def _default_starter(job: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=job, name="summary", daemon=True)
    thread.start()
    return thread


class SummaryController:
    def __init__(  # noqa: PLR0913 - the ports the composition root wires
        self,
        repository: MeetingRepository,
        lock: MeetingLock,
        review: ReviewController,
        files: SummaryFiles,
        clock: Clock,
        lease: GpuLease,
        keys: CloudKeyStore,
        config: SummaryConfig,
        *,
        events: SummaryEvents | None = None,
        start_job: JobStarter | None = None,
        on_gpu_free: Callable[[], None] | None = None,
    ) -> None:
        self._repository = repository
        self._lock = lock
        self._review = review
        self._files = files
        self._clock = clock
        self._lease = lease
        self._keys = keys
        self._config = config
        self._events: SummaryEvents = events or NullSummaryEvents()
        self._start_job: JobStarter = start_job or _default_starter
        self._on_gpu_free = on_gpu_free
        self._slot = threading.Condition()  # guards the job, _stopping, _spawning, _thread
        self._spawning = False  # a claimed job whose thread is being started (outside the lock)
        self._job: _Job | None = None
        self._thread: object = None
        self._stopping = False

    # -- commands (any thread) --

    @property
    def active_meeting_id(self) -> str:
        with self._slot:
            return self._job.meeting_id if self._job else ""

    def providers(self) -> tuple[ProviderRow, ProviderRow]:
        """Local is not probed; the cloud row says only whether it is enabled (no key read)."""
        config = self._config
        local, cloud = config.local, config.cloud
        return (
            self._row(local, available=True, reason=""),
            self._row(
                cloud,
                available=config.cloud_enabled,
                reason="" if config.cloud_enabled else SummaryCloudDisabledError.error_code,
            ),
        )

    def summarize(
        self, meeting_id: str, provider_id: str, cloud_confirmed: bool
    ) -> Result[str, MeetingScribeError]:
        """Start a summary and return ``RUNNING`` at once; the model runs on the job thread."""
        option = self._option(provider_id)
        if isinstance(option, Failure):
            return option
        gated = self._cloud_gate(option.unwrap(), cloud_confirmed)
        if isinstance(gated, Failure):
            return gated
        with self._lock.hold():
            snapshot = self._snapshot(meeting_id, option.unwrap())
        if isinstance(snapshot, Failure):
            return snapshot
        job = _Job(meeting_id, option.unwrap(), snapshot.unwrap())
        claimed = self._claim(job)
        if isinstance(claimed, Failure):
            return claimed
        if job.option.cloud:  # only now, for a confirmed request that nothing else refused
            key = self._keys.get_key()
            if isinstance(key, Failure):
                self._finish_slot(job)
                return key
        logger.info(
            "Summary started: meeting=%s provider=%s model=%s",
            meeting_id,
            job.option.id,
            job.option.model,
        )
        self._events.active_changed()
        started = self._spawn(job)
        if isinstance(started, Failure):
            self._events.active_changed()  # the slot was freed again
        return started.map(lambda _: "RUNNING")

    def cancel(self, meeting_id: str) -> Result[bool, MeetingScribeError]:
        """Cancel the running summary of this meeting; ``False`` when none is running."""
        known = self._meeting(meeting_id)
        if isinstance(known, Failure):
            return known
        with self._slot:
            if self._job is None or self._job.meeting_id != meeting_id:
                return Success(False)
            self._job.cancel.set()
            return Success(True)

    def shutdown(self) -> None:
        """Abort the running summary and wait for its thread; nothing is persisted."""
        with self._slot:
            self._stopping = True
            if self._job is not None:
                self._job.cancel.set()
            self._slot.wait_for(lambda: not self._spawning)  # a claimed job is spawned or refused
            thread = self._thread
        if isinstance(thread, threading.Thread) and thread is not threading.current_thread():
            thread.join()

    # -- reads --

    def get_status(self, meeting_id: str) -> Result[SummaryStatus, MeetingScribeError]:
        with self._lock.hold():
            return self._meeting(meeting_id).bind(self._status)

    def get_summary(self, meeting_id: str) -> Result[SummaryDocument, MeetingScribeError]:
        with self._lock.hold():
            return self._meeting(meeting_id).bind(self._document)

    # -- the job thread --

    def _spawn(self, job: _Job) -> Result[None, MeetingScribeError]:
        """Start the job thread without holding the slot lock; ``shutdown`` waits for the flag."""
        with self._slot:
            if self._stopping:
                self._release_locked(job)
                stopped = True
            else:
                self._spawning = True
                stopped = False
        failure: MeetingScribeError | None = None
        if stopped:
            failure = SummaryBusyError("The service is shutting down.")
        else:
            thread: object = None
            try:
                thread = self._start_job(lambda: self._run(job))
            except Exception:
                logger.exception("Could not start the summary job for %s.", job.meeting_id)
                failure = MeetingScribeError("The summary could not be started.")
            with self._slot:
                if failure is None:
                    self._thread = thread
                else:
                    self._release_locked(job)
                self._spawning = False
                self._slot.notify_all()
        if failure is None:
            return Success(None)
        self._gpu_freed(job)
        return Failure(failure)

    def _run(self, job: _Job) -> None:
        settled: tuple[SummaryOutcome, str] = ("FAILED", MeetingScribeError.error_code)
        try:
            try:
                result = job.option.generate.run(
                    job.request,
                    job.cancel,
                    lambda step, total: self._progress(job, step, total),
                    self._config.total_timeout_seconds,
                )
                settled = self._settle(job, result)
            except Exception:  # the thread must always end the job
                logger.exception("The summary of meeting %s stopped unexpectedly.", job.meeting_id)
                settled = self._record_failure(job, MeetingScribeError("The summary stopped."))
        except Exception:
            logger.exception("The failure of summary %s could not be recorded.", job.meeting_id)
        finally:
            self._finish_slot(job)  # never leaks the slot or the GPU lease
        self._emit(lambda: self._events.finished(job.meeting_id, *settled))
        self._emit(self._events.active_changed)

    @staticmethod
    def _emit(call: Callable[[], None]) -> None:
        try:
            call()
        except Exception:
            logger.exception("A summary observer failed.")

    def _progress(self, job: _Job, step: int, total: int) -> None:
        with self._slot:
            job.step, job.total = step, total
        self._events.progress(job.meeting_id, step, total)

    def _settle(
        self, job: _Job, result: Result[GeneratedSummary, MeetingScribeError]
    ) -> tuple[SummaryOutcome, str]:
        if isinstance(result, Failure):
            error = result.failure()
            if isinstance(error, SummaryCancelledError):
                return "CANCELLED", error.error_code  # nothing is written, nothing recorded
            return self._record_failure(job, error)
        saved = self._save(job, result.unwrap())
        if isinstance(saved, Failure):
            return self._record_failure(job, saved.failure())
        return "DONE", ""

    def _save(self, job: _Job, generated: GeneratedSummary) -> Result[None, MeetingScribeError]:
        """File first, then ``meeting.json``, both under the lock and only on a ``DONE`` meeting."""
        with self._lock.hold():
            current = self._done(job.meeting_id)
            if isinstance(current, Failure):
                return current
            meeting = current.unwrap()
            written = self._files.save(meeting.directory, generated.text.markdown)
            if isinstance(written, Failure):
                return written
            request = job.request
            info = SummaryInfo(
                file=written.unwrap(),
                generated_at=request.generated_at,
                provider=job.option.id,
                cloud=job.option.cloud,
                model=job.option.model,
                language=request.language,
                title=generated.text.title,
                input_fingerprint=generated.input_fingerprint,
                chunks=generated.chunks,
                prompt_version=generated.prompt_version,
            )
            return self._repository.save(meeting.with_summary(info)).map(lambda _: None)

    def _record_failure(self, job: _Job, error: MeetingScribeError) -> tuple[SummaryOutcome, str]:
        """Remember the failed attempt (code and time) unless the meeting is being processed."""
        code = error.error_code
        try:
            with self._lock.hold():
                current = self._done(job.meeting_id)
                if isinstance(current, Success):
                    marked = current.unwrap().with_summary_failure(code, self._clock.now())
                    saved = self._repository.save(marked)
                    if isinstance(saved, Failure):
                        logger.warning("Could not record the summary failure: %s", code)
        except Exception:  # log the id and the code only: never the error text
            logger.error("Could not record the failure %s of summary %s.", code, job.meeting_id)
        logger.warning("Summary of meeting %s failed: %s", job.meeting_id, code)
        return "FAILED", code

    def _claim(self, job: _Job) -> Result[None, MeetingScribeError]:
        """Take the single slot and, for a local summary, the GPU lease."""
        with self._slot:
            if self._stopping:
                return Failure(SummaryBusyError("The service is shutting down."))
            if self._job is not None:
                if self._job.meeting_id == job.meeting_id:
                    return Failure(
                        SummaryAlreadyRunningError("A summary of this meeting is already running.")
                    )
                return Failure(
                    SummaryBusyError("Another meeting is being summarized; wait for it.")
                )
            if not job.option.cloud and not self._lease.acquire("summary", job.cancel.set):
                return Failure(
                    SummaryGpuBusyError(
                        "A recording or processing job is using the GPU. "
                        "Try again when processing ends, or use the cloud provider."
                    )
                )
            self._job = job
            return Success(None)

    def _release_locked(self, job: _Job) -> None:
        """Caller holds the slot lock: free the slot and the lease."""
        if self._job is job:
            self._job = None
        if not job.option.cloud:
            try:
                self._lease.release("summary")
            except Exception:  # the events and the queue wake-up must still follow
                logger.exception("Could not release the GPU lease of summary %s.", job.meeting_id)

    def _finish_slot(self, job: _Job) -> None:
        """Free the slot and the GPU (the model is already released), then wake the queue."""
        with self._slot:
            self._release_locked(job)
        self._gpu_freed(job)

    def _gpu_freed(self, job: _Job) -> None:
        if not job.option.cloud and self._on_gpu_free is not None:
            self._emit(self._on_gpu_free)

    # -- snapshot and reads (called with the lock held) --

    def _option(self, provider_id: str) -> Result[ConfiguredProvider, MeetingScribeError]:
        for option in (self._config.local, self._config.cloud):
            if option.id == provider_id:
                return Success(option)
        return Failure(InvalidValueError(f"Unknown summary provider '{provider_id}'."))

    def _cloud_gate(
        self, option: ConfiguredProvider, confirmed: bool
    ) -> Result[None, MeetingScribeError]:
        if not option.cloud:
            return Success(None)
        if not self._config.cloud_enabled:
            return Failure(
                SummaryCloudDisabledError(
                    "The cloud provider is off. Enable SUMMARIES_CLOUD_ENABLED."
                )
            )
        if not confirmed:
            return Failure(
                SummaryCloudNotConfirmedError("Confirm sending this meeting to the cloud provider.")
            )
        return Success(None)

    def _meeting(self, meeting_id: str) -> Result[Meeting, MeetingScribeError]:
        loaded = self._repository.get(meeting_id)
        if isinstance(loaded, Failure):
            return loaded
        return loaded.unwrap().as_result(
            EntityNotFoundError(f"Meeting '{meeting_id}' does not exist.", detail_code="MEETING")
        )

    def _done(self, meeting_id: str) -> Result[Meeting, MeetingScribeError]:
        found = self._meeting(meeting_id)
        if isinstance(found, Failure):
            return found
        if found.unwrap().processing.state != "DONE":
            return Failure(
                MeetingNotProcessedError(
                    f"Meeting '{meeting_id}' is {found.unwrap().processing.state}; "
                    "a summary needs a finished meeting."
                )
            )
        return found

    def _snapshot(
        self, meeting_id: str, option: ConfiguredProvider
    ) -> Result[SummaryRequest, MeetingScribeError]:
        found = self._done(meeting_id)
        if isinstance(found, Failure):
            return found
        meeting = found.unwrap()
        material = self._material(meeting_id)
        if isinstance(material, Failure):
            return material
        participants, lines = material.unwrap()
        if not lines:
            return Failure(SummaryNoSpeechError("There is nothing to summarize."))
        language = self._config.language
        if language == "auto":
            language = meeting.processing.detected_language or "en"
        return Success(
            SummaryRequest(
                started_at=meeting.recording.started_at,
                duration_seconds=_duration(meeting),
                generated_at=self._clock.now(),
                participants=participants,
                lines=lines,
                language=language,
                provider_label=option.label,
                limits=option.limits,
            )
        )

    def _material(
        self, meeting_id: str
    ) -> Result[tuple[tuple[str, ...], tuple[SummaryLine, ...]], MeetingScribeError]:
        """Display names and the effective utterances, as the review app shows them."""
        speakers = self._review.speakers(meeting_id)
        if isinstance(speakers, Failure):
            return speakers
        utterances = self._review.transcript(meeting_id)
        if isinstance(utterances, Failure):
            return utterances
        names = {speaker.id: speaker.display for speaker in speakers.unwrap()}
        participants = tuple(s.display for s in speakers.unwrap() if not s.merged_into)
        lines = tuple(
            SummaryLine(
                speaker=names.get(u.speaker, u.speaker), start=u.start, end=u.end, text=u.text
            )
            for u in utterances.unwrap()
        )
        return Success((participants, lines))

    def _is_stale(self, meeting: Meeting, info: SummaryInfo) -> Result[bool, MeetingScribeError]:
        if meeting.processing.state != "DONE":
            return Success(True)  # being reprocessed: the transcript is about to be replaced
        material = self._material(meeting.id)
        if isinstance(material, Failure):
            return material
        _, lines = material.unwrap()
        return Success(input_fingerprint(lines) != info.input_fingerprint)

    def _status(self, meeting: Meeting) -> Result[SummaryStatus, MeetingScribeError]:
        info = meeting.summary
        with self._slot:
            job = self._job if self._job and self._job.meeting_id == meeting.id else None
        has_file = bool(info and info.file and self._files.exists(meeting.directory))
        failure = info.last_failure.error_code if info and info.last_failure else ""
        if job is not None:
            return Success(
                SummaryStatus(
                    "RUNNING",
                    stale=False,
                    cloud=job.option.cloud,
                    provider_label=job.option.label,
                    generated_at=info.generated_at if info and has_file else None,
                    step=job.step,
                    total_steps=job.total,
                    failure_code="",
                )
            )
        if info is None or not (has_file or failure):
            return Success(SummaryStatus("NONE", False, False, "", None, 0, 0, ""))
        stale = self._is_stale(meeting, info) if has_file else Success(False)
        if isinstance(stale, Failure):
            return stale
        return Success(
            SummaryStatus(
                "FAILED" if failure else "DONE",
                stale=stale.unwrap(),
                cloud=info.cloud if has_file else False,
                provider_label=self._label(info) if has_file else "",
                generated_at=info.generated_at if has_file else None,
                step=0,
                total_steps=0,
                failure_code=failure,
            )
        )

    def _document(self, meeting: Meeting) -> Result[SummaryDocument, MeetingScribeError]:
        info = meeting.summary
        if info is None or not info.file:
            return Failure(
                EntityNotFoundError("The meeting has no summary.", detail_code="SUMMARY")
            )
        text = self._files.read(meeting.directory)
        if isinstance(text, Failure):
            return text
        stale = self._is_stale(meeting, info)
        if isinstance(stale, Failure):
            return stale
        return Success(
            SummaryDocument(
                markdown=text.unwrap(),
                title=info.title,
                generated_at=info.generated_at,
                provider=info.provider,
                provider_label=self._label(info),
                cloud=info.cloud,
                model=info.model,
                language=info.language,
                stale=stale.unwrap(),
            )
        )

    def _label(self, info: SummaryInfo) -> str:
        for option in (self._config.local, self._config.cloud):
            if option.id == info.provider:
                return option.label
        return info.provider

    def _row(self, option: ConfiguredProvider, *, available: bool, reason: str) -> ProviderRow:
        return ProviderRow(
            id=option.id,
            label=option.label,
            cloud=option.cloud,
            model=option.model,
            available=available,
            reason=reason,
            default=option.id == self._config.default_provider,
        )


def _duration(meeting: Meeting) -> float:
    if meeting.processing.audio is not None:
        return meeting.processing.audio.duration_seconds
    stopped = meeting.recording.stopped_at
    return (stopped - meeting.recording.started_at).total_seconds() if stopped else 0.0
