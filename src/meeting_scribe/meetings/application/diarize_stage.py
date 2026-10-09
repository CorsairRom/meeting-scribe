"""The ``diarize`` worker stage as a use case: audio in, protocol events out (spec 6.1, 6.2).

Runs inside the worker process through ports, so it is tested with fakes. It depends on the
``speakers`` domain only through that package's application layer.
"""

import os
from collections.abc import Callable
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.meetings.domain.meeting import AudioInfo, SpeakerEntry
from meeting_scribe.meetings.domain.ports import AudioChannelReader, AudioInspector
from meeting_scribe.meetings.domain.timeline import Timeline, audio_fingerprint
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    ResultEvent,
    StageEvent,
    StartedEvent,
    WorkerEvent,
    WorkerJob,
)
from meeting_scribe.speakers.application.assemble_transcript import (
    AssembleTranscript,
    AssemblyRequest,
    SpeakerSummary,
)
from meeting_scribe.speakers.application.diarize_audio import DiarizationRequest, DiarizeAudio
from meeting_scribe.transcription.application.load_words import LoadWords

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

    def extracting_samples(self) -> None:
        self._emit(StageEvent(state="EXTRACTING_SAMPLES"))


def run_diarize_stage(  # noqa: PLR0911 - one early return per failing step
    job: WorkerJob,
    *,
    audio: AudioInspector,
    channels: AudioChannelReader,
    words: LoadWords,
    diarize: DiarizeAudio,
    assemble: AssembleTranscript,
    emit: Emit,
) -> Result[ResultEvent, MeetingScribeError]:
    meeting_dir = Path(job.meeting_dir)
    inspected = audio.inspect(meeting_dir)
    if isinstance(inspected, Failure):
        return inspected
    timeline = inspected.unwrap()
    transcription = words.run(meeting_dir)  # before any GPU work: no words, nothing to do
    if isinstance(transcription, Failure):
        return transcription
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
    reporter = _EventReporter(emit)
    diarized = diarize.run(meeting_dir, request.unwrap(), reporter)
    if isinstance(diarized, Failure):
        return diarized
    done = diarized.unwrap()
    assembly = AssemblyRequest.from_spec(
        job.speakers,
        transcription.unwrap().words,
        done.result,
        done.channel,
        energies.unwrap().mic,
        energies.unwrap().system,
        lambda channel: channels.read_channel(meeting_dir, timeline, channel),
    )
    if isinstance(assembly, Failure):
        return assembly
    assembled = assemble.run(meeting_dir, assembly.unwrap(), reporter)
    if isinstance(assembled, Failure):
        return assembled
    result = assembled.unwrap()
    return Success(
        ResultEvent(
            artifacts={"turns": done.artifact, **result.artifacts},
            speakers=tuple(_entry(speaker) for speaker in result.speakers),
            detected_language=transcription.unwrap().detected_language,
            audio=_audio_info(timeline),
        )
    )


def _entry(speaker: SpeakerSummary) -> SpeakerEntry:
    """The speakers domain's summary as the ``meeting.json`` entry; ``name`` stays empty (F4)."""
    return SpeakerEntry(
        id=speaker.id,
        label=speaker.label,
        is_me=speaker.is_me,
        talk_seconds=speaker.talk_seconds,
        utterance_count=speaker.utterance_count,
        sample=speaker.sample,
    )


def _audio_info(timeline: Timeline) -> AudioInfo:
    return AudioInfo(
        chunk_count=len(timeline.chunks),
        total_frames=timeline.total_frames,
        duration_seconds=timeline.duration_seconds,
    )
