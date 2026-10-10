"""Word to speaker assignment (spec 3, ``assign_speakers``)."""

import itertools
from bisect import bisect_left, bisect_right
from collections.abc import Sequence

from meeting_scribe.speakers.domain.turns import SpokenWord, TimedWord, Turn

FALLBACK_SPEAKER = "SPEAKER_FALLBACK"
"""Owner of every word when diarization returned no turns (it is numbered ``Speaker 1``)."""


def assign_speakers(words: Sequence[TimedWord], turns: Sequence[Turn]) -> tuple[SpokenWord, ...]:
    """Give each word the turn with the largest positive overlap, else the nearest by midpoint.

    Ties go to the earlier turn. Only the turns that can overlap the word are examined (two
    bisects over the sorted starts and the running maximum of the ends), so the cost is near
    linear in words for a meeting-sized exclusive diarization.
    """
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end))
    index = _Index(
        ordered,
        [turn.start for turn in ordered],
        list(itertools.accumulate((turn.end for turn in ordered), max)),
    )
    return tuple(_assign(word, index) for word in words)


class _Index:
    def __init__(self, turns: Sequence[Turn], starts: list[float], max_ends: list[float]) -> None:
        self.turns, self.starts, self.max_ends = turns, starts, max_ends

    def candidates(self, word: TimedWord) -> Sequence[Turn]:
        """Turns that may overlap: they start before the word ends and the first of them that
        can still be running at the word's start is found through the prefix maximum of ends."""
        low = bisect_right(self.max_ends, word.start)  # every turn before ends by word.start
        high = bisect_left(self.starts, word.end)  # every turn from here starts at/after the end
        return self.turns[low:high]


def _assign(word: TimedWord, index: _Index) -> SpokenWord:
    return SpokenWord(start=word.start, end=word.end, text=word.text, speaker=_owner(word, index))


def _owner(word: TimedWord, index: _Index) -> str:
    turns = index.turns
    if not turns:
        return FALLBACK_SPEAKER
    best_overlap, owner = 0.0, None
    for turn in index.candidates(word):
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        if overlap > best_overlap:
            best_overlap, owner = overlap, turn.speaker
    if owner is not None:
        return owner
    midpoint = (word.start + word.end) / 2
    return min(turns, key=lambda turn: _distance(midpoint, turn)).speaker  # min keeps the first


def _distance(point: float, turn: Turn) -> float:
    return max(turn.start - point, point - turn.end, 0.0)
