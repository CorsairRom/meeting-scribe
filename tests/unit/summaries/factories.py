"""Synthetic builders for the summaries tests: obviously fake names and text only."""

from datetime import UTC, datetime

from meeting_scribe.summaries.domain.request import CompletionLimits, SummaryLine, SummaryRequest

STARTED_AT = datetime(2031, 3, 4, 14, 5, tzinfo=UTC)
GENERATED_AT = datetime(2031, 3, 5, 9, 30, tzinfo=UTC)


def line(
    speaker: str = "Person A", start: float = 0.0, text: str = "alpha beta gamma."
) -> SummaryLine:
    return SummaryLine(speaker=speaker, start=start, end=start + 2.0, text=text)


def limits(
    context_tokens: int = 4096, max_output_tokens: int = 512, notes_output_tokens: int | None = None
) -> CompletionLimits:
    return CompletionLimits(
        model="fake-model",
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        context_tokens=context_tokens,
        notes_output_tokens=notes_output_tokens,
    )


def request(
    lines: tuple[SummaryLine, ...] | None = None,
    language: str = "en",
    context_tokens: int = 4096,
    max_output_tokens: int = 512,
    notes_output_tokens: int | None = None,
) -> SummaryRequest:
    return SummaryRequest(
        started_at=STARTED_AT,
        duration_seconds=2700.0,
        generated_at=GENERATED_AT,
        participants=("Person A", "Person B"),
        lines=lines if lines is not None else (line(), line("Person B", 5.0, "delta epsilon.")),
        language=language,
        provider_label="Fake provider",
        limits=limits(context_tokens, max_output_tokens, notes_output_tokens),
    )
