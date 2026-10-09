"""Value objects of the speakers domain: who spoke when, and what was said (spec 3)."""

from pydantic import BaseModel, ConfigDict

ME = "ME"
"""Speaker key of the user's own voice (decided by energy dominance, spec 2.4)."""
UNKNOWN = "UNKNOWN"
"""Speaker key of mic-only speech that is neither clearly the user nor a system speaker."""

_UNNUMBERED = {ME: "Me", UNKNOWN: "Unknown"}


class Turn(BaseModel):
    """One speaker talking from ``start`` to ``end`` (seconds from the session start)."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return self.end - self.start


class TimedWord(BaseModel):
    """A transcribed word; the transcription domain's ``Word`` is mapped to this one."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str


class SpokenWord(TimedWord):
    speaker: str


class Utterance(BaseModel):
    """Consecutive words of one speaker: one paragraph of the transcript."""

    model_config = ConfigDict(frozen=True)

    speaker: str
    start: float
    end: float
    text: str


class SpeakerLabel(BaseModel):
    """How a speaker is listed: a stable ``id`` (``ME``, ``UNKNOWN``, ``S1``), a label, a name."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    name: str = ""

    @property
    def display(self) -> str:
        return self.name or self.label

    def with_name(self, name: str) -> "SpeakerLabel":
        return self.model_copy(update={"name": name.strip()})


def unnumbered_label(speaker: str) -> SpeakerLabel | None:
    """The fixed label of ``ME`` and ``UNKNOWN``; ``None`` for a diarization speaker."""
    label = _UNNUMBERED.get(speaker)
    return SpeakerLabel(id=speaker, label=label) if label else None
