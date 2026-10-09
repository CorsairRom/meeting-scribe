"""Diarize one meeting's system (or, in person, microphone) channel, or reuse ``turns.json``."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import InvalidValueError, MeetingScribeError
from meeting_scribe.speakers.domain.diarization import (
    TURNS_FILE,
    Device,
    DiarizationResult,
    build_cache_key,
    cache_document,
    read_cached,
)
from meeting_scribe.speakers.domain.me_detection import DiarizedChannel, choose_diarization_channel
from meeting_scribe.speakers.domain.params import DiarizationParams
from meeting_scribe.speakers.domain.ports import Diarizer, TurnsCache

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.0


class DiarizationReporter(Protocol):
    """Where the stage's events go (the worker turns them into protocol events)."""

    def started(self, device_label: str) -> None: ...

    def cache_hit(self, artifact: str) -> None: ...

    def progress(self, processed_seconds: float, total_seconds: float) -> None: ...


class DiarizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    params: DiarizationParams
    device: Device
    fingerprint: Sequence[tuple[str, int, int]]
    duration_seconds: float
    system_energies: Sequence[float]
    """The 100 ms envelope of the system channel: decides whether this was an in-person meeting."""
    load_channel: Callable[[DiarizedChannel], Result[Samples, MeetingScribeError]]
    """Called only on a cache miss: decoding is the expensive part when the cache is valid."""

    @classmethod
    def from_spec(
        cls,
        spec: Mapping[str, JsonValue],
        device: str,
        fingerprint: Sequence[tuple[str, int, int]],
        duration_seconds: float,
        system_energies: Sequence[float],
        load_channel: Callable[[DiarizedChannel], Result[Samples, MeetingScribeError]],
    ) -> Result[Self, InvalidValueError]:
        """Build a request from the job spec's ``speakers`` section; never raises."""
        try:
            return Success(
                cls.model_validate(
                    {
                        "params": dict(spec),
                        "device": device,
                        "fingerprint": fingerprint,
                        "duration_seconds": duration_seconds,
                        "system_energies": system_energies,
                        "load_channel": load_channel,
                    }
                )
            )
        except ValidationError as error:
            fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in error.errors())
            return Failure(InvalidValueError(f"Invalid speakers job spec: {fields}."))


class DiarizationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: str = TURNS_FILE
    channel: DiarizedChannel
    result: DiarizationResult
    cached: bool


class DiarizeAudio:
    def __init__(
        self,
        diarizer: Diarizer,
        cache: TurnsCache,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._diarizer = diarizer
        self._cache = cache
        self._monotonic = monotonic

    def run(
        self, meeting_dir: Path, request: DiarizationRequest, reporter: DiarizationReporter
    ) -> Result[DiarizationOutcome, MeetingScribeError]:
        channel = choose_diarization_channel(
            request.system_energies, request.params.me_noise_margin_db
        )
        key = build_cache_key(
            request.params,
            request.device,
            channel,
            self._diarizer.engine_versions(),
            request.fingerprint,
        )
        hit = read_cached(self._cache.load(meeting_dir), key).value_or(None)
        if hit is not None:
            reporter.cache_hit(TURNS_FILE)
            return Success(DiarizationOutcome(channel=channel, result=hit, cached=True))
        if channel == "mic":
            logger.info("The system channel is silent: diarizing the microphone (in person).")
        if request.device == "cpu":
            logger.warning("Diarizing on the CPU, as configured (PROCESSING_DEVICE=cpu).")
        reporter.started(request.device)
        audio = request.load_channel(channel)
        if isinstance(audio, Failure):
            return audio
        diarized = self._diarizer.diarize(
            audio.unwrap(),
            request.params,
            request.device,
            self._throttled(reporter, request.duration_seconds),
        )
        if isinstance(diarized, Failure):
            return diarized
        result = diarized.unwrap()
        stored = self._cache.store(meeting_dir, cache_document(key, result))
        if isinstance(stored, Failure):
            return stored
        return Success(DiarizationOutcome(channel=channel, result=result, cached=False))

    def _throttled(
        self, reporter: DiarizationReporter, total_seconds: float
    ) -> Callable[[float], None]:
        last = float("-inf")

        def report(fraction: float) -> None:
            nonlocal last
            now = self._monotonic()
            if now - last >= PROGRESS_INTERVAL_SECONDS:
                last = now
                reporter.progress(min(max(fraction, 0.0), 1.0) * total_seconds, total_seconds)

        return report
