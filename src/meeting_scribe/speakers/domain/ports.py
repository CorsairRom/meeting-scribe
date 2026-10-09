"""Ports the speakers adapters implement (the pipeline, the cache file)."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from returns.result import Result

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.domain.diarization import Device, DiarizationResult
from meeting_scribe.speakers.domain.errors import DiarizationError
from meeting_scribe.speakers.domain.params import DiarizationParams
from meeting_scribe.speakers.domain.samples import Clip
from meeting_scribe.speakers.domain.turns import SpeakerLabel, Utterance


class Diarizer(Protocol):
    def engine_versions(self) -> dict[str, str]:
        """Versions that change the output (part of the cache key); must not load a model."""
        ...

    def diarize(
        self,
        audio: Samples,
        params: DiarizationParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[DiarizationResult, MeetingScribeError]:
        """Load the pipeline, diarize the whole mono signal, release the pipeline.

        ``on_progress`` receives the fraction done (0..1). Never raises; never falls back to
        another device than ``device``.
        """
        ...


class TurnsCache(Protocol):
    def load(self, meeting_dir: Path) -> dict[str, object]:
        """The stored document; ``{}`` (the null document) when absent or unreadable."""
        ...

    def store(
        self, meeting_dir: Path, document: dict[str, object]
    ) -> Result[None, DiarizationError]:
        """Write atomically."""
        ...


class TranscriptFiles(Protocol):
    """The files the extraction step writes into the meeting folder (all atomic)."""

    def reset_samples(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        """Remove the voice samples of a previous run (speakers may have changed)."""
        ...

    def write_sample(
        self, meeting_dir: Path, speaker_id: str, signal: Samples, clips: Sequence[Clip]
    ) -> Result[str, DiarizationError]:
        """Join the clips of ``signal`` into ``samples/<speaker_id>.wav``; the relative path."""
        ...

    def write_utterances(
        self,
        meeting_dir: Path,
        utterances: Sequence[Utterance],
        labels: Mapping[str, SpeakerLabel],
    ) -> Result[str, DiarizationError]:
        """``utterances.json``, each utterance under its stable speaker id; the file name."""
        ...

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, DiarizationError]:
        """``transcript.md``; the file name."""
        ...
