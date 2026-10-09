"""The ``diarize`` worker stage as a use case: audio in, protocol events out (spec 6.1, 6.2).

Runs inside the worker process through ports, so it is tested with fakes. It depends on the
``speakers`` domain only through that package's application layer.
"""

import os
from collections.abc import Callable
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.meeting import AudioInfo
from meeting_scribe.meetings.domain.ports import AudioChannelReader, AudioInspector
from meeting_scribe.meetings.domain.timeline import Timeline, audio_fingerprint
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    ResultEvent,
    StartedEvent,
    WorkerEvent,
    WorkerJob,
)
from meeting_scribe.speakers.application.diarize_audio import DiarizationRequest, DiarizeAudio

type Emit = Callable[[WorkerEvent], None]


class _EventReporter:
    """Turns the diarization service's notifications into protocol events."""

    def __init__(self, emit: Emit) -> None:
        self._emit = emit

    def started(self, device_label: str) -> None:
        self._emit(StartedEvent(pid=os.getpid(), device=device_label))

    def cache_hit(self, artifact: str) -> None:
        self._emit(CacheHitEvent(artifact=artifact))

    def progress(self, processed_seconds: float, total_seconds: float) -> None:
        self._emit(
            ProgressEvent(
                stage="diarize",
                processed_seconds=processed_seconds,
                total_seconds=total_seconds,
            )
        )


def run_diarize_stage(
    job: WorkerJob,
    *,
    audio: AudioInspector,
    channels: AudioChannelReader,
    diarize: DiarizeAudio,
    emit: Emit,
) -> Result[ResultEvent, MeetingScribeError]:
    meeting_dir = Path(job.meeting_dir)
    inspected = audio.inspect(meeting_dir)
    if isinstance(inspected, Failure):
        return inspected
    timeline = inspected.unwrap()
    energies = channels.read_energies(meeting_dir, timeline)
    if isinstance(energies, Failure):
        return energies
    request = DiarizationRequest.from_spec(
        job.speakers,
        job.device,
        audio_fingerprint(timeline),
        timeline.duration_seconds,
        energies.unwrap().system,
        lambda channel: channels.read_channel(meeting_dir, timeline, channel),
    )
    if isinstance(request, Failure):
        return request
    outcome = diarize.run(meeting_dir, request.unwrap(), _EventReporter(emit))
    if isinstance(outcome, Failure):
        return outcome
    return Success(
        ResultEvent(artifacts={"turns": outcome.unwrap().artifact}, audio=_audio_info(timeline))
    )


def _audio_info(timeline: Timeline) -> AudioInfo:
    return AudioInfo(
        chunk_count=len(timeline.chunks),
        total_frames=timeline.total_frames,
        duration_seconds=timeline.duration_seconds,
    )
