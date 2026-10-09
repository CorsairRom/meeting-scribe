"""Utterance grouping (spec 3, ``group_utterances``)."""

from collections.abc import Sequence

from meeting_scribe.speakers.domain.turns import SpokenWord, Utterance


def group_utterances(words: Sequence[SpokenWord], max_gap: float) -> tuple[Utterance, ...]:
    """Join consecutive words of one speaker while the silence between them is ``<= max_gap``."""
    utterances: list[Utterance] = []
    current: list[SpokenWord] = []
    for word in words:
        if current and not _continues(current[-1], word, max_gap):
            utterances.append(_utterance(current))
            current = []
        current.append(word)
    if current:
        utterances.append(_utterance(current))
    return tuple(utterances)


def _continues(previous: SpokenWord, word: SpokenWord, max_gap: float) -> bool:
    return word.speaker == previous.speaker and word.start - previous.end <= max_gap


def _utterance(words: Sequence[SpokenWord]) -> Utterance:
    return Utterance(
        speaker=words[0].speaker,
        start=words[0].start,
        end=max(word.end for word in words),
        text=" ".join(word.text for word in words),
    )
