"""Speaker review rules: names, merges and the effective view (review-app spec 2 and 9).

Pure functions over values. Edits are stored non-destructively: a name lives on its record and a
merge is an entry of ``merged_into``; utterances and samples are never touched, so an unmerge is
exact. Error messages carry speaker ids only, never a name (names are private).
"""

import unicodedata

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, InvalidValueError
from meeting_scribe.speakers.domain.errors import SpeakerNameTakenError

MAX_NAME_LENGTH = 64
"""Longest name, in code points, after NFC normalisation and trimming."""

_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})


class SpeakerRecord(BaseModel):
    """A speaker as stored in ``meeting.json``: the F3 result plus the user's name."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    name: str = ""
    is_me: bool = False
    talk_seconds: float = 0.0
    utterance_count: int = 0
    sample: str = ""

    @property
    def display(self) -> str:
        return self.name or self.label


class ReviewState(BaseModel):
    """Every speaker plus the merge map ``source id -> target id`` (never chained)."""

    model_config = ConfigDict(frozen=True)

    speakers: tuple[SpeakerRecord, ...]
    merged_into: dict[str, str]

    def find(self, speaker_id: str) -> SpeakerRecord | None:
        return next((s for s in self.speakers if s.id == speaker_id), None)

    def is_active(self, speaker_id: str) -> bool:
        return speaker_id not in self.merged_into


def validate_name(raw: str) -> Result[str, InvalidValueError]:
    """NFC, trimmed; empty clears the name; at most 64 code points; no control or format chars."""
    name = unicodedata.normalize("NFC", raw).strip()
    if len(name) > MAX_NAME_LENGTH:
        return Failure(
            InvalidValueError(f"A speaker name has at most {MAX_NAME_LENGTH} characters.")
        )
    if any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in name):
        return Failure(InvalidValueError("A speaker name cannot contain control characters."))
    return Success(name)


def rename(
    state: ReviewState, speaker_id: str, raw_name: str
) -> Result[ReviewState, InvalidValueError | EntityNotFoundError]:
    """Set (or, when empty, clear) the name of one speaker."""
    target = state.find(speaker_id)
    if target is None:
        return Failure(_unknown_speaker(speaker_id))
    validated = validate_name(raw_name)
    if isinstance(validated, Failure):
        return validated
    name = validated.unwrap()
    if name and _is_taken(state, speaker_id, name):
        return Failure(SpeakerNameTakenError(f"That name is already used (speaker {speaker_id})."))
    return Success(_replace(state, target.model_copy(update={"name": name})))


def _is_taken(state: ReviewState, speaker_id: str, name: str) -> bool:
    wanted = name.casefold()
    for other in state.speakers:
        if other.id == speaker_id or not state.is_active(other.id):
            continue
        if wanted in (other.display.casefold(), other.label.casefold()):
            return True
    return False


def _replace(state: ReviewState, record: SpeakerRecord) -> ReviewState:
    speakers = tuple(record if s.id == record.id else s for s in state.speakers)
    return state.model_copy(update={"speakers": speakers})


def _unknown_speaker(speaker_id: str) -> EntityNotFoundError:
    return EntityNotFoundError(f"Unknown speaker {speaker_id}.", detail_code="SPEAKER")
