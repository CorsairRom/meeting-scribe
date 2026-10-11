"""Frozen value types of the review app, built by the parsers from D-Bus plain values."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MeetingSummary:
    """One row of ``Meetings1.List``."""

    id: str
    started_at: datetime
    duration_seconds: float
    state: str
    active_speaker_count: int


@dataclass(frozen=True, slots=True)
class Speaker:
    """One row of ``Meetings1.GetSpeakers`` (merged totals are already folded in by the service)."""

    id: str
    label: str
    name: str
    display: str
    is_me: bool
    talk_seconds: float
    utterance_count: int
    has_sample: bool
    merged_into: str

    @property
    def is_active(self) -> bool:
        return not self.merged_into


@dataclass(frozen=True, slots=True)
class Utterance:
    """One row of ``Meetings1.GetTranscript``; ``speaker_id`` is the effective id after merges."""

    speaker_id: str
    start_seconds: float
    end_seconds: float
    text: str
