import json
from pathlib import Path

from returns.result import Failure

from meeting_scribe.speakers.domain.diarization import (
    TURNS_FILE,
    build_cache_key,
    cache_document,
    read_cached,
)
from meeting_scribe.speakers.infrastructure.json_turns_cache import JsonTurnsCache
from tests.unit.speakers.fakes import FINGERPRINT, PARAMS, RESULT, VERSIONS


def test_a_stored_document_round_trips_and_matches_its_key(tmp_path: Path) -> None:
    key = build_cache_key(PARAMS, "cuda", "system", VERSIONS, FINGERPRINT)
    cache = JsonTurnsCache()

    cache.store(tmp_path, cache_document(key, RESULT)).unwrap()

    assert read_cached(cache.load(tmp_path), key).unwrap() == RESULT
    assert not (tmp_path / f".{TURNS_FILE}.tmp").exists()


def test_a_missing_or_damaged_file_is_the_null_document(tmp_path: Path) -> None:
    cache = JsonTurnsCache()
    assert cache.load(tmp_path) == {}
    (tmp_path / TURNS_FILE).write_bytes(b"\xff\xfe not json")
    assert cache.load(tmp_path) == {}
    (tmp_path / TURNS_FILE).write_text("[1, 2]", encoding="utf-8")
    assert cache.load(tmp_path) == {}


def test_a_write_failure_is_a_diarization_error(tmp_path: Path) -> None:
    result = JsonTurnsCache().store(tmp_path / "missing", {"a": 1})

    assert isinstance(result, Failure)
    assert result.failure().error_code == "DIARIZATION_FAILED"


def test_the_key_is_json_native() -> None:
    key = build_cache_key(PARAMS, "cpu", "mic", VERSIONS, FINGERPRINT)

    assert json.loads(json.dumps(key)) == key
