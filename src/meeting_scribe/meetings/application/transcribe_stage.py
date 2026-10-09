"""The ``transcribe`` worker stage as a use case: audio in, protocol events out (spec 6.1, 6.2).

Runs inside the worker process through ports, so it is tested with fakes. It depends on the
``transcription`` domain only through that package's application layer.
"""

import os
from collections.abc import Callable
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.meeting import AudioInfo
from meeting_scribe.meetings.domain.ports import AudioInspector, AudioMixReader
from meeting_scribe.meetings.domain.timeline import Timeline, audio_fingerprint
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    ResultEvent,
    StartedEvent,
    WorkerEvent,
    WorkerJob,
)
from meeting_scribe.transcription.application.transcribe_audio import (
    TranscribeAudio,
    TranscriptionRequest,
)

type Emit = Callable[[WorkerEvent], None]


class _EventReporter:
    """Turns the transcription service's notifications into protocol events."""

    def __init__(self, emit: Emit) -> None:
        self._emit = emit

    def started(self, device_label: str) -> None:
        self._emit(StartedEvent(pid=os.getpid(), device=device_label))

    def cache_hit(self, artifact: str) -> None:
        self._emit(CacheHitEvent(artifact=artifact))

    def progress(self, processed_seconds: float, total_seconds: float) -> None:
        self._emit(
            ProgressEvent(
                stage="transcribe",
                processed_seconds=processed_seconds,
                total_seconds=total_seconds,
            )
        )


def run_transcribe_stage(
    job: WorkerJob,
    *,
    audio: AudioInspector,
    mix: AudioMixReader,
    transcribe: TranscribeAudio,
    emit: Emit,
) -> Result[ResultEvent, MeetingScribeError]:
    meeting_dir = Path(job.meeting_dir)
    inspected = audio.inspect(meeting_dir)
    if isinstance(inspected, Failure):
        return inspected
    timeline = inspected.unwrap()
    request = TranscriptionRequest.from_spec(
        job.transcription,
        job.device,
        audio_fingerprint(timeline),
        timeline.duration_seconds,
        lambda: mix.read_mix(meeting_dir, timeline),
    )
    if isinstance(request, Failure):
        return request
    outcome = transcribe.run(meeting_dir, request.unwrap(), _EventReporter(emit))
    if isinstance(outcome, Failure):
        return outcome
    done = outcome.unwrap()
    return Success(
        ResultEvent(
            artifacts={"words": done.artifact},
            detected_language=done.detected_language,
            audio=_audio_info(timeline),
        )
    )


def _audio_info(timeline: Timeline) -> AudioInfo:
    return AudioInfo(
        chunk_count=len(timeline.chunks),
        total_frames=timeline.total_frames,
        duration_seconds=timeline.duration_seconds,
    )
