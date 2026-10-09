import json
from collections.abc import Mapping

from meeting_scribe.transcription.domain.cache import (
    Fingerprint,
    build_cache_key,
    cache_document,
    read_cached,
)
from meeting_scribe.transcription.domain.params import Device, TranscriptionParams
from meeting_scribe.transcription.domain.words import TranscriptionResult, Word
from tests.unit.transcription.fakes import FINGERPRINT, PARAMS, VERSIONS, WORDS

RESULT = TranscriptionResult(detected_language="es", words=WORDS)


def _key(
    params: TranscriptionParams = PARAMS,
    device: Device = "cuda",
    versions: Mapping[str, str] = VERSIONS,
    fingerprint: Fingerprint = FINGERPRINT,
):
    return build_cache_key(params, device, versions, fingerprint)


def _stored(key) -> dict:
    return json.loads(json.dumps(cache_document(key, RESULT)))  # as read back from words.json


def test_a_matching_document_gives_the_cached_result_back() -> None:
    cached = read_cached(_stored(_key()), _key())

    assert cached.unwrap() == RESULT


def test_cache_is_invalid_when_params_or_audio_change() -> None:
    stored = _stored(_key())
    changed = {
        "language": _key(PARAMS.model_copy(update={"language": "auto"})),
        "model": _key(PARAMS.model_copy(update={"model": "small"})),
        "beam": _key(PARAMS.model_copy(update={"beam_size": 1})),
        "vad": _key(PARAMS.model_copy(update={"vad_filter": False})),
        "previous text": _key(PARAMS.model_copy(update={"condition_on_previous_text": True})),
        "device": _key(device="cpu"),
        "engine": _key(versions={**VERSIONS, "ctranslate2": "5.0.0"}),
        "frame count": _key(
            fingerprint=((*FINGERPRINT[0][:1], 1, FINGERPRINT[0][2]), FINGERPRINT[1])
        ),
        "chunk list": _key(fingerprint=FINGERPRINT[:1]),
    }

    for name, key in changed.items():
        assert not read_cached(stored, key).value_or(None), name


def test_the_compute_type_of_the_other_device_does_not_invalidate_the_cache() -> None:
    other = PARAMS.model_copy(update={"cpu_compute_type": "int8_float32"})

    assert read_cached(_stored(_key()), _key(other)).unwrap() == RESULT


def test_garbage_is_a_miss_never_an_error() -> None:
    key = _key()

    for document in (
        {},
        {"cache_version": 99, "key": {}, "data": {}},
        {"key": key},
        {"cache_version": 1, "key": key, "data": {"words": [{"start": "x"}]}},
    ):
        assert read_cached(document, key).value_or(None) is None


def test_the_document_never_escapes_non_ascii_text() -> None:
    result = TranscriptionResult(
        detected_language="es", words=(Word(start=0, end=1, text="mañana", probability=1.0),)
    )

    assert "mañana" in json.dumps(cache_document(_key(), result), ensure_ascii=False)
