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
    """Output budget of the final summary (and its repair)."""
    context_tokens: int
    notes_output_tokens: int | None = None
    """Output budget of a map or merge step, which writes short notes; ``None`` means the same."""

    def for_notes(self) -> "CompletionLimits":
        """The limits of a notes step: the same, with the small notes output budget."""
        if self.notes_output_tokens is None:
            return self
        return self.model_copy(update={"max_output_tokens": self.notes_output_tokens})

    @property
    def largest_output_tokens(self) -> int:
        return max(self.max_output_tokens, self.notes_output_tokens or 0)


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
        """Context minus the largest output budget minus the margin: what one request may carry."""
        return self.limits.context_tokens - self.limits.largest_output_tokens - MARGIN_TOKENS
