"""Diarization output and its ``turns.json`` cache (spec 4, 6.4)."""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from returns.maybe import Maybe, Nothing, Some
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import GpuUnavailableError
from meeting_scribe.speakers.domain.me_detection import DiarizedChannel
from meeting_scribe.speakers.domain.params import DiarizationParams
from meeting_scribe.speakers.domain.turns import Turn

type Device = Literal["cuda", "cpu"]

CACHE_VERSION = 1
TURNS_FILE = "turns.json"

type CacheKey = dict[str, JsonValue]
type Fingerprint = Sequence[tuple[str, int, int]]
"""``(file name, frames, size in bytes)`` per chunk, in order (as in the meetings timeline)."""


class DiarizationResult(BaseModel):
    """Both pyannote annotations of one session, as plain turns.

    ``turns`` may overlap (regular diarization: clean-turn detection for samples); ``exclusive``
    never does (word assignment).
    """

    model_config = ConfigDict(frozen=True)

    turns: tuple[Turn, ...]
    exclusive: tuple[Turn, ...]


def check_device_available(
    device: Device, cuda_device_count: int
) -> Result[Device, GpuUnavailableError]:
    """``cuda`` without a CUDA device fails; nothing here (or after) falls back to the CPU."""
    if device == "cuda" and cuda_device_count < 1:
        return Failure(
            GpuUnavailableError(
                "No usable CUDA device was found. Fix the NVIDIA driver, or set "
                "PROCESSING_DEVICE=cpu to run on the CPU explicitly (slow)."
            )
        )
    return Success(device)


def build_cache_key(
    params: DiarizationParams,
    device: Device,
    channel: DiarizedChannel,
    engine_versions: Mapping[str, str],
    fingerprint: Fingerprint,
) -> CacheKey:
    """JSON-native on purpose: a key compares equal to itself after a file round trip."""
    return {
        "params": {
            "pipeline": params.pipeline,
            "num_speakers": params.num_speakers,
            "min_speakers": params.min_speakers,
            "max_speakers": params.max_speakers,
            "device": device,
            "channel": channel,
        },
        "engines": dict(engine_versions),
        "audio": [[name, frames, size] for name, frames, size in fingerprint],
    }


def cache_document(key: CacheKey, result: DiarizationResult) -> dict[str, object]:
    return {
        "cache_version": CACHE_VERSION,
        "key": key,
        "data": {
            "diarization": [turn.model_dump() for turn in result.turns],
            "exclusive_diarization": [turn.model_dump() for turn in result.exclusive],
        },
    }


def read_cached(document: Mapping[str, object], key: CacheKey) -> Maybe[DiarizationResult]:
    """The cached result when ``document`` was written for exactly ``key``; otherwise ``Nothing``.

    Anything unexpected is a miss, never an error: the stage diarizes again and overwrites it.
    """
    if document.get("cache_version") != CACHE_VERSION or document.get("key") != key:
        return Nothing
    data = document.get("data")
    if not isinstance(data, dict):
        return Nothing
    try:
        return Some(
            DiarizationResult(
                turns=tuple(Turn.model_validate(item) for item in data["diarization"]),
                exclusive=tuple(
                    Turn.model_validate(item) for item in data["exclusive_diarization"]
                ),
            )
        )
    except (KeyError, TypeError, ValidationError):
        return Nothing
