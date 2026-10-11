"""Speaker review rules: names, merges and the effective view (review-app spec 2 and 9).

Pure functions over values. Edits are stored non-destructively: a name lives on its record and a
merge is an entry of ``merged_into``; utterances and samples are never touched, so an unmerge is
exact. Error messages carry speaker ids only, never a name (names are private).
"""

import unicodedata
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, InvalidValueError
from meeting_scribe.speakers.domain.errors import SpeakerMergeInvalidError, SpeakerNameTakenError
from meeting_scribe.speakers.domain.turns import Utterance

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


def merge(
    state: ReviewState, source_id: str, target_id: str
) -> Result[ReviewState, SpeakerMergeInvalidError | EntityNotFoundError]:
    """Show ``source`` as ``target``. "Me" can be a target, never a source; targets never chain."""
    source, target = state.find(source_id), state.find(target_id)
    for speaker_id, record in ((source_id, source), (target_id, target)):
        if record is None:
            return Failure(_unknown_speaker(speaker_id))
    if source is None or target is None:  # unreachable; narrows the types
        return Failure(_unknown_speaker(source_id))
    if source.is_me:
        return Failure(SpeakerMergeInvalidError('"Me" cannot be merged into another speaker.'))
    if source_id == target_id:
        return Failure(SpeakerMergeInvalidError(f"Speaker {source_id} cannot merge into itself."))
    if not state.is_active(source_id) or not state.is_active(target_id):
        return Failure(SpeakerMergeInvalidError("A merged speaker cannot be a source or target."))
    merged_into = {
        child: (target_id if parent == source_id else parent)
        for child, parent in state.merged_into.items()
    }
    merged_into[source_id] = target_id
    adopted = (
        target
        if target.name or not source.name
        else target.model_copy(update={"name": source.name})
    )
    return Success(_replace(state.model_copy(update={"merged_into": merged_into}), adopted))


def unmerge(state: ReviewState, speaker_id: str) -> Result[ReviewState, EntityNotFoundError]:
    """Make a merged speaker active again with its own record; a no-op when it is not merged."""
    if state.find(speaker_id) is None:
        return Failure(_unknown_speaker(speaker_id))
    merged_into = {k: v for k, v in state.merged_into.items() if k != speaker_id}
    return Success(state.model_copy(update={"merged_into": merged_into}))


class EffectiveSpeaker(BaseModel):
    """One ``GetSpeakers`` row: the speaker as the user sees it after names and merges."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    name: str
    display: str
    is_me: bool
    talk_seconds: float
    utterance_count: int
    has_sample: bool
    merged_into: str


def effective_speakers(state: ReviewState) -> tuple[EffectiveSpeaker, ...]:
    """Active speakers first ("Me", then by talk time), merged ones after; targets fold totals."""
    talk: dict[str, float] = {s.id: s.talk_seconds for s in state.speakers}
    count: dict[str, int] = {s.id: s.utterance_count for s in state.speakers}
    for source_id, target_id in state.merged_into.items():
        talk[target_id] += talk[source_id]
        count[target_id] += count[source_id]
    ordered = sorted(
        state.speakers,
        key=lambda s: (not state.is_active(s.id), not s.is_me, -talk[s.id]),
    )
    return tuple(
        EffectiveSpeaker(
            id=s.id,
            label=s.label,
            name=s.name,
            display=s.display,
            is_me=s.is_me,
            talk_seconds=round(talk[s.id], 3),
            utterance_count=count[s.id],
            has_sample=bool(s.sample),
            merged_into=state.merged_into.get(s.id, ""),
        )
        for s in ordered
    )


def effective_utterances(
    utterances: Sequence[Utterance], merged_into: Mapping[str, str]
) -> tuple[Utterance, ...]:
    """The utterances with every merged speaker shown as its target; nothing else changes."""
    return tuple(
        u.model_copy(update={"speaker": merged_into[u.speaker]}) if u.speaker in merged_into else u
        for u in utterances
    )


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
