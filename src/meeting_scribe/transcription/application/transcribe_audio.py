"""Transcribe one meeting's signal, or reuse the cached words (spec 4, 6.2)."""

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.transcription.domain.cache import (
    WORDS_FILE,
    build_cache_key,
    cache_document,
    read_cached,
)
from meeting_scribe.transcription.domain.params import Device, TranscriptionParams
from meeting_scribe.transcription.domain.ports import Transcriber, WordsCache

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.0


class TranscriptionReporter(Protocol):
    """Where the stage's events go (the worker turns them into protocol events)."""

    def started(self, device_label: str) -> None: ...

    def cache_hit(self, artifact: str) -> None: ...

    def progress(self, processed_seconds: float, total_seconds: float) -> None: ...


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    params: TranscriptionParams
    device: Device
    fingerprint: Sequence[tuple[str, int, int]]
    duration_seconds: float
    load_audio: Callable[[], Result[Samples, MeetingScribeError]]
    """Called only on a cache miss: decoding is the expensive part when the cache is valid."""


class TranscriptionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    detected_language: str
    word_count: int
    cached: bool


class TranscribeAudio:
    def __init__(
        self,
        transcriber: Transcriber,
        cache: WordsCache,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transcriber = transcriber
        self._cache = cache
        self._monotonic = monotonic

    def run(
        self, meeting_dir: Path, request: TranscriptionRequest, reporter: TranscriptionReporter
    ) -> Result[TranscriptionOutcome, MeetingScribeError]:
        key = build_cache_key(
            request.params, request.device, self._transcriber.engine_versions(), request.fingerprint
        )
        hit = read_cached(self._cache.load(meeting_dir), key).value_or(None)
        if hit is not None:
            reporter.cache_hit(WORDS_FILE)
            return Success(
                TranscriptionOutcome(
                    detected_language=hit.detected_language,
                    word_count=len(hit.words),
                    cached=True,
                )
            )
        if request.device == "cpu":
            logger.warning("Transcribing on the CPU, as configured (PROCESSING_DEVICE=cpu).")
        reporter.started(request.params.device_label(request.device))
        audio = request.load_audio()
        if isinstance(audio, Failure):
            return audio
        transcribed = self._transcriber.transcribe(
            audio.unwrap(),
            request.params,
            request.device,
            self._throttled(reporter, request.duration_seconds),
        )
        if isinstance(transcribed, Failure):
            return transcribed
        result = transcribed.unwrap()
        stored = self._cache.store(meeting_dir, cache_document(key, result))
        if isinstance(stored, Failure):
            return stored
        return Success(
            TranscriptionOutcome(
                detected_language=result.detected_language,
                word_count=len(result.words),
                cached=False,
            )
        )

    def _throttled(
        self, reporter: TranscriptionReporter, total_seconds: float
    ) -> Callable[[float], None]:
        last = float("-inf")

        def report(processed_seconds: float) -> None:
            nonlocal last
            now = self._monotonic()
            if now - last >= PROGRESS_INTERVAL_SECONDS:
                last = now
                reporter.progress(min(processed_seconds, total_seconds), total_seconds)

        return report
