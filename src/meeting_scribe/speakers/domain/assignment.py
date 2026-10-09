"""Word to speaker assignment (spec 3, ``assign_speakers``)."""

from collections.abc import Sequence

from meeting_scribe.speakers.domain.turns import SpokenWord, TimedWord, Turn

FALLBACK_SPEAKER = "SPEAKER_FALLBACK"
"""Owner of every word when diarization returned no turns (it is numbered ``Speaker 1``)."""


def assign_speakers(words: Sequence[TimedWord], turns: Sequence[Turn]) -> tuple[SpokenWord, ...]:
    """Give each word the turn with the largest positive overlap, else the nearest by midpoint.

    Ties go to the earlier turn. Cost is words x turns, fine for a meeting (about 10^7 steps).
    """
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end))
    return tuple(_assign(word, ordered) for word in words)


def _assign(word: TimedWord, turns: Sequence[Turn]) -> SpokenWord:
    return SpokenWord(start=word.start, end=word.end, text=word.text, speaker=_owner(word, turns))


def _owner(word: TimedWord, turns: Sequence[Turn]) -> str:
    if not turns:
        return FALLBACK_SPEAKER
    best_overlap, owner = 0.0, None
    for turn in turns:
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        if overlap > best_overlap:
            best_overlap, owner = overlap, turn.speaker
    if owner is not None:
        return owner
    midpoint = (word.start + word.end) / 2
    return min(turns, key=lambda turn: _distance(midpoint, turn)).speaker  # min keeps the first


def _distance(point: float, turn: Turn) -> float:
    return max(turn.start - point, point - turn.end, 0.0)
