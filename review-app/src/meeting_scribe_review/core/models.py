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


@dataclass(frozen=True, slots=True)
class ProviderRow:
    """One row of ``Summaries1.Providers``; ``label`` is worded by the service."""

    id: str
    label: str
    cloud: bool
    model: str
    available: bool
    reason: str
    default: bool


@dataclass(frozen=True, slots=True)
class SummaryInfo:
    """``Summaries1.GetSummaryStatus``: no content. ``generated_at`` is ``None`` without a file."""

    state: str
    stale: bool
    cloud: bool
    provider_label: str
    generated_at: datetime | None
    step: int
    total_steps: int
    failure_code: str

    @property
    def has_summary(self) -> bool:
        return self.generated_at is not None


@dataclass(frozen=True, slots=True)
class SummaryDocument:
    """``Summaries1.GetSummary``: the text and its facts. Private user data; never logged."""

    markdown: str
    title: str
    generated_at: datetime
    provider_label: str
    cloud: bool
    model: str
    language: str
    stale: bool
