"""Word and segment value objects produced by a transcription."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict


class Word(BaseModel):
    """One word with session-relative timestamps in seconds."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str
    probability: float = 1.0


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()


class TranscriptionResult(BaseModel):
    """Everything later stages need: the words in order and the language Whisper settled on."""

    model_config = ConfigDict(frozen=True)

    detected_language: str
    words: tuple[Word, ...]

    @classmethod
    def from_segments(
        cls, detected_language: str, segments: Iterable[Segment]
    ) -> "TranscriptionResult":
        words = tuple(word for segment in segments for word in segment.words)
        return cls(detected_language=detected_language, words=words)
