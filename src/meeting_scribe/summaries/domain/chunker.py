"""Token estimate and chunking on utterance boundaries (spec 4)."""

import math
import re
from collections.abc import Callable, Sequence

from meeting_scribe.summaries.domain.request import SummaryLine

CHARS_PER_TOKEN = 2.25
"""Characters per token, a deliberate undercount: no tokenizer dependency (assumption A-7).

Measured 2026-10-12 with the qwen3:4b-instruct tokenizer on a Spanish meeting dialogue
(timestamps and speaker prefixes): 22,397 characters were 8,883 prompt tokens, 2.52 characters
per token. The old value of 3 undercounted tokens by about 16% and one request overran the
window. 2.25 is that ratio minus about 10%. Other models and languages differ; the truncation
guard still catches a miss.
"""
SEPARATOR = "\n"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fits(text: str, budget_tokens: int) -> bool:
    return estimate_tokens(text) <= budget_tokens


def format_line(line: SummaryLine) -> str:
    seconds = int(line.start)
    stamp = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"[{stamp}] {line.speaker}: {line.text}"


def chunk_transcript(lines: Sequence[SummaryLine], budget_tokens: int) -> tuple[str, ...]:
    """Pack formatted lines into chunks that each fit ``budget_tokens``, in order.

    A line is never dropped or duplicated; one that alone exceeds the budget is split on
    sentences, then on characters, each piece repeating the speaker prefix.
    """
    pieces = [piece for line in lines for piece in _pieces(line, budget_tokens)]
    return tuple(SEPARATOR.join(batch) for batch in pack_texts(pieces, budget_tokens))


def pack_texts(
    texts: Sequence[str],
    budget_tokens: int,
    render: Callable[[Sequence[str]], str] = SEPARATOR.join,
) -> tuple[tuple[str, ...], ...]:
    """Group consecutive texts while the group, as ``render`` shows it, fits the budget.

    An oversized text stays alone. ``render`` must produce what is actually sent.
    """
    batches: list[list[str]] = []
    for text in texts:
        if batches and fits(render([*batches[-1], text]), budget_tokens):
            batches[-1].append(text)
        else:
            batches.append([text])
    return tuple(tuple(batch) for batch in batches)


def _pieces(line: SummaryLine, budget_tokens: int) -> list[str]:
    whole = format_line(line)
    if fits(whole, budget_tokens):
        return [whole]
    prefix = format_line(line.model_copy(update={"text": ""}))
    room = max(1, int(budget_tokens * CHARS_PER_TOKEN) - len(prefix))
    return [prefix + part for part in _split(line.text, room)]


def _split(text: str, room: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= room:
            current = candidate
            continue
        if current:
            parts.append(current)
        rest = sentence
        while len(rest) > room:
            parts.append(rest[:room])
            rest = rest[room:]
        current = rest
    if current:
        parts.append(current)
    return parts
