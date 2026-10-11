"""Client-side speaker name checks for instant feedback. The service stays authoritative.

Mirrors ``validate_name`` and the uniqueness rule of the service (spec section 9): NFC, trimmed,
empty clears, at most 64 code points, no control (``Cc``) or format (``Cf``) characters, and unique
(ignoring case) among the other active speakers' display names and default labels.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.result import Err, Ok, Result

MAX_NAME_LENGTH = 64
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})


class NameProblemKind(StrEnum):
    TOO_LONG = "too_long"
    CONTROL_CHARACTER = "control_character"
    TAKEN = "taken"


@dataclass(frozen=True, slots=True)
class NameProblem:
    kind: NameProblemKind
    speaker_id: str = ""  # TAKEN: the active speaker that already uses the name


_MESSAGES = {
    NameProblemKind.TOO_LONG: f"A name has at most {MAX_NAME_LENGTH} characters.",
    NameProblemKind.CONTROL_CHARACTER: "A name cannot contain line breaks or control characters.",
    NameProblemKind.TAKEN: "That name is already used by another speaker.",
}


def problem_message(problem: NameProblem) -> str:
    return _MESSAGES[problem.kind]


def check_name(raw: str, speaker_id: str, speakers: Sequence[Speaker]) -> Result[str, NameProblem]:
    """The normalised name (possibly empty, which clears it) or the first problem found."""
    name = unicodedata.normalize("NFC", raw).strip()
    if len(name) > MAX_NAME_LENGTH:
        return Err(NameProblem(NameProblemKind.TOO_LONG))
    if any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in name):
        return Err(NameProblem(NameProblemKind.CONTROL_CHARACTER))
    if name:
        wanted = name.casefold()
        for other in speakers:
            if (
                other.id != speaker_id
                and other.is_active
                and wanted
                in (
                    other.display.casefold(),
                    other.label.casefold(),
                )
            ):
                return Err(NameProblem(NameProblemKind.TAKEN, speaker_id=other.id))
    return Ok(name)
