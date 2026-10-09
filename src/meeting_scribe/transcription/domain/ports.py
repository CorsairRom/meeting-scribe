"""Ports the transcription adapters implement (the model, the cache file)."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from returns.result import Result

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.params import Device, TranscriptionParams
from meeting_scribe.transcription.domain.words import TranscriptionResult


class Transcriber(Protocol):
    def engine_versions(self) -> dict[str, str]:
        """Versions that change the output (part of the cache key); must not load a model."""
        ...

    def transcribe(
        self,
        audio: Samples,
        params: TranscriptionParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[TranscriptionResult, MeetingScribeError]:
        """Load the model, transcribe the whole signal, release the model.

        ``on_progress`` receives the end time in seconds of every finished segment. Never raises;
        never falls back to another device than ``device``.
        """
        ...


class WordsCache(Protocol):
    def load(self, meeting_dir: Path) -> dict[str, object]:
        """The stored document; ``{}`` (the null document) when absent or unreadable."""
        ...

    def store(
        self, meeting_dir: Path, document: dict[str, object]
    ) -> Result[None, TranscriptionError]:
        """Write atomically."""
        ...
