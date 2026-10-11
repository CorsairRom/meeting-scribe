"""Plain input of a summary: facts, names, utterance lines, language and budget (spec 3)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

MARGIN_TOKENS = 600
"""Reserved per request for the instructions and a safety margin (spec 4)."""


class SummaryLine(BaseModel):
    """One effective utterance: the display name, the seconds from the start and the text."""

    model_config = ConfigDict(frozen=True)

    speaker: str
    start: float
    end: float
    text: str


class CompletionLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    temperature: float
    max_output_tokens: int
    context_tokens: int


class SummaryRequest(BaseModel):
    """Everything a summary needs; carries no meeting id, path or secret (AC-7)."""

    model_config = ConfigDict(frozen=True)

    started_at: datetime
    duration_seconds: float
    generated_at: datetime
    participants: tuple[str, ...]
    lines: tuple[SummaryLine, ...]
    language: str
    provider_label: str
    limits: CompletionLimits

    @property
    def usable_input_tokens(self) -> int:
        """Context minus the output limit minus the margin: what one request may carry."""
        return self.limits.context_tokens - self.limits.max_output_tokens - MARGIN_TOKENS
