"""Ports the speakers adapters implement (the pipeline, the cache file)."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from returns.result import Result

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.domain.diarization import Device, DiarizationResult
from meeting_scribe.speakers.domain.errors import DiarizationError
from meeting_scribe.speakers.domain.params import DiarizationParams


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
