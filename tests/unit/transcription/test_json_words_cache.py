from pathlib import Path

import pytest
from returns.result import Failure

from meeting_scribe.transcription.domain.cache import WORDS_FILE
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.infrastructure.json_words_cache import JsonWordsCache


def test_a_stored_document_reads_back_unchanged_with_no_temporary_file(tmp_path: Path) -> None:
    cache = JsonWordsCache()
    document: dict[str, object] = {"cache_version": 1, "data": {"text": "mañana"}}

    assert cache.store(tmp_path, document).unwrap() is None

    assert cache.load(tmp_path) == document
    assert "mañana" in (tmp_path / WORDS_FILE).read_text(encoding="utf-8")
    assert [path.name for path in tmp_path.iterdir()] == [WORDS_FILE]


def test_a_missing_file_is_no_cache_and_not_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        assert JsonWordsCache().load(tmp_path) == {}

    assert caplog.records == []


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"\xff\xfe\x00 not utf-8", b"[1, 2, 3]", b'"text"'],
    ids=["bad json", "bad utf-8", "list document", "string document"],
)
def test_a_damaged_or_non_object_file_is_no_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: bytes
) -> None:
    (tmp_path / WORDS_FILE).write_bytes(content)

    with caplog.at_level("WARNING"):
        assert JsonWordsCache().load(tmp_path) == {}

    # Bad JSON and bad UTF-8 are reported; a valid but non-dict document is silently a miss.
    if content in (b"{not json", b"\xff\xfe\x00 not utf-8"):
        assert WORDS_FILE in caplog.text


def test_a_failed_write_returns_the_error_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old: dict[str, object] = {"cache_version": 1}
    cache = JsonWordsCache()
    cache.store(tmp_path, old).unwrap()

    def refuse(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    failed = cache.store(tmp_path, {"cache_version": 2})

    assert isinstance(failed, Failure)
    assert isinstance(failed.failure(), TranscriptionError)
    assert cache.load(tmp_path) == old
    assert [path.name for path in tmp_path.iterdir()] == [WORDS_FILE]


def test_an_unwritable_directory_is_a_transcription_error(tmp_path: Path) -> None:
    failed = JsonWordsCache().store(tmp_path / "missing", {"a": 1})

    assert isinstance(failed.failure(), TranscriptionError)
    assert WORDS_FILE in str(failed.failure())
