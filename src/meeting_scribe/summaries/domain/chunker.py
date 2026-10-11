"""Token estimate and chunking on utterance boundaries (spec 4)."""

import re
from collections.abc import Sequence

from meeting_scribe.summaries.domain.request import SummaryLine

CHARS_PER_TOKEN = 3
"""Conservative for Spanish and English; no tokenizer dependency (assumption A-7)."""
SEPARATOR = "\n"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


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


def pack_texts(texts: Sequence[str], budget_tokens: int) -> tuple[tuple[str, ...], ...]:
    """Group consecutive texts while the joined group fits; an oversized text stays alone."""
    batches: list[list[str]] = []
    for text in texts:
        if batches and fits(SEPARATOR.join([*batches[-1], text]), budget_tokens):
            batches[-1].append(text)
        else:
            batches.append([text])
    return tuple(tuple(batch) for batch in batches)


def _pieces(line: SummaryLine, budget_tokens: int) -> list[str]:
    whole = format_line(line)
    if fits(whole, budget_tokens):
        return [whole]
    prefix = format_line(line.model_copy(update={"text": ""}))
    room = max(1, budget_tokens * CHARS_PER_TOKEN - len(prefix))
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
