from pathlib import Path

from meeting_scribe.transcription.application.load_words import LoadWords
from meeting_scribe.transcription.domain.cache import cache_document
from meeting_scribe.transcription.domain.words import TranscriptionResult
from tests.unit.transcription.fakes import WORDS, InMemoryWordsCache

MEETING = Path("/meetings/m1")


def test_the_stored_words_and_language_are_returned_whatever_the_key() -> None:
    cache = InMemoryWordsCache()
    cache.document = cache_document(
        {"anything": 1}, TranscriptionResult(detected_language="es", words=WORDS)
    )

    result = LoadWords(cache).run(MEETING).unwrap()

    assert (result.detected_language, result.words) == ("es", WORDS)


def test_a_missing_or_damaged_file_means_transcribe_first() -> None:
    cache = InMemoryWordsCache()
    documents: list[dict[str, object]] = [
        {},
        {"data": []},
        {"data": {"words": [{"start": "x"}], "detected_language": "es"}},
    ]
    for document in documents:
        cache.document = document

        failure = LoadWords(cache).run(MEETING).failure()

        assert failure.error_code == "TRANSCRIPTION_FAILED"
        assert "transcribe" in str(failure)
