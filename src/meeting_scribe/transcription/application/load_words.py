"""Read the words ``words.json`` holds, for the stage that assigns them to speakers (spec 3)."""

from pathlib import Path

from pydantic import ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.ports import WordsCache
from meeting_scribe.transcription.domain.words import TranscriptionResult, Word


class LoadWords:
    def __init__(self, cache: WordsCache) -> None:
        self._cache = cache

    def run(self, meeting_dir: Path) -> Result[TranscriptionResult, TranscriptionError]:
        """The stored transcription, whatever key it was written for (that is the transcribe
        stage's business).

        The diarize stage never transcribes: a missing or unreadable file means the transcription
        has not run, which fails this job instead of producing an empty meeting.
        """
        data = self._cache.load(meeting_dir).get("data")
        if not isinstance(data, dict):
            return Failure(
                TranscriptionError("words.json is missing or unreadable; transcribe first.")
            )
        try:
            words = tuple(Word.model_validate(item) for item in data["words"])
            return Success(
                TranscriptionResult(detected_language=data["detected_language"], words=words)
            )
        except (KeyError, TypeError, ValidationError):
            return Failure(
                TranscriptionError("words.json has an unexpected layout; transcribe again.")
            )
