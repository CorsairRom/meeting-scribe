"""``words.json``: a result cache keyed on parameters, engines and the exact audio (spec 4)."""

from collections.abc import Mapping, Sequence

from pydantic import JsonValue, ValidationError
from returns.maybe import Maybe, Nothing, Some

from meeting_scribe.transcription.domain.params import Device, TranscriptionParams
from meeting_scribe.transcription.domain.words import TranscriptionResult, Word

CACHE_VERSION = 1
WORDS_FILE = "words.json"

type CacheKey = dict[str, JsonValue]
type Fingerprint = Sequence[tuple[str, int, int]]
"""``(file name, frames, size in bytes)`` per chunk, in order (as in the meetings timeline)."""


def build_cache_key(
    params: TranscriptionParams,
    device: Device,
    engine_versions: Mapping[str, str],
    fingerprint: Fingerprint,
) -> CacheKey:
    """JSON-native on purpose: a key compares equal to itself after a file round trip."""
    return {
        "params": {
            "model": params.model,
            "language": params.language,
            "compute_type": params.effective_compute_type(device),
            "device": device,
            "beam_size": params.beam_size,
            "vad_filter": params.vad_filter,
            "condition_on_previous_text": params.condition_on_previous_text,
            "word_timestamps": True,
        },
        "engines": dict(engine_versions),
        "audio": [[name, frames, size] for name, frames, size in fingerprint],
    }


def cache_document(key: CacheKey, result: TranscriptionResult) -> dict[str, object]:
    return {
        "cache_version": CACHE_VERSION,
        "key": key,
        "data": {
            "detected_language": result.detected_language,
            "words": [word.model_dump() for word in result.words],
        },
    }


def read_cached(document: Mapping[str, object], key: CacheKey) -> Maybe[TranscriptionResult]:
    """The cached result when ``document`` was written for exactly ``key``; otherwise ``Nothing``.

    Anything unexpected (a missing field, another version, a different key) is a miss, never an
    error: the stage simply transcribes again and overwrites the file.
    """
    if document.get("cache_version") != CACHE_VERSION or document.get("key") != key:
        return Nothing
    data = document.get("data")
    if not isinstance(data, dict):
        return Nothing
    try:
        words = tuple(Word.model_validate(item) for item in data["words"])
        return Some(TranscriptionResult(detected_language=data["detected_language"], words=words))
    except (KeyError, TypeError, ValidationError):
        return Nothing
