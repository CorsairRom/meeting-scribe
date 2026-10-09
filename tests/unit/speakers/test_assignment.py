import random

from meeting_scribe.speakers.domain.assignment import FALLBACK_SPEAKER, assign_speakers
from meeting_scribe.speakers.domain.turns import TimedWord, Turn

S1 = Turn(start=0.0, end=2.0, speaker="S1")
S2 = Turn(start=2.5, end=5.0, speaker="S2")


def _speakers(words: list[TimedWord], turns: list[Turn]) -> list[str]:
    return [word.speaker for word in assign_speakers(words, turns)]


def test_max_overlap_then_nearest_by_midpoint() -> None:
    words = [TimedWord(start=1.8, end=2.2, text="a"), TimedWord(start=2.3, end=2.4, text="b")]

    assert _speakers(words, [S1, S2]) == ["S1", "S2"]  # 0.2 s vs 0.0 s; midpoint 2.35 nearer S2


def test_the_word_straddling_two_turns_goes_to_the_one_it_overlaps_most() -> None:
    word = TimedWord(start=1.9, end=2.9, text="x")  # 0.1 s in S1, 0.4 s in S2

    assert _speakers([word], [S1, S2]) == ["S2"]


def test_a_tie_goes_to_the_earlier_turn_whatever_the_input_order() -> None:
    word = TimedWord(start=1.0, end=3.0, text="x")
    turns = [Turn(start=2.0, end=4.0, speaker="late"), Turn(start=0.0, end=2.0, speaker="early")]

    assert _speakers([word], turns) == ["early"]


def test_a_gap_between_equally_distant_turns_goes_to_the_earlier_turn() -> None:
    word = TimedWord(start=2.2, end=2.3, text="x")  # midpoint 2.25: 0.25 from both
    turns = [Turn(start=0.0, end=2.0, speaker="a"), Turn(start=2.5, end=4.5, speaker="b")]

    assert _speakers([word], turns) == ["a"]


def test_a_word_before_the_first_turn_and_after_the_last_gets_the_nearest() -> None:
    words = [TimedWord(start=-3.0, end=-2.0, text="a"), TimedWord(start=9.0, end=9.5, text="b")]

    assert _speakers(words, [S1, S2]) == ["S1", "S2"]


def test_a_zero_length_word_inside_a_turn_belongs_to_it() -> None:
    assert _speakers([TimedWord(start=3.0, end=3.0, text="a")], [S1, S2]) == ["S2"]


def test_no_turns_gives_every_word_the_fallback_speaker_and_no_words_gives_nothing() -> None:
    words = [TimedWord(start=0.0, end=1.0, text="a"), TimedWord(start=1.0, end=2.0, text="b")]

    assert _speakers(words, []) == [FALLBACK_SPEAKER, FALLBACK_SPEAKER]
    assert assign_speakers([], [S1]) == ()


def test_the_word_keeps_its_timing_and_text() -> None:
    assigned = assign_speakers([TimedWord(start=0.5, end=0.9, text="hola")], [S1])[0]

    assert (assigned.start, assigned.end, assigned.text) == (0.5, 0.9, "hola")


def _brute_force(word: TimedWord, turns: list[Turn]) -> str:
    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    best, owner = 0.0, None
    for turn in ordered:
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        if overlap > best:
            best, owner = overlap, turn.speaker
    if owner is not None:
        return owner
    mid = (word.start + word.end) / 2
    return min(ordered, key=lambda t: max(t.start - mid, mid - t.end, 0.0)).speaker


def test_the_indexed_search_agrees_with_checking_every_turn() -> None:
    rng = random.Random(7)  # noqa: S311 - a reproducible test fixture
    turns = [
        Turn(start=s, end=s + rng.uniform(0.2, 30.0), speaker=f"S{i}")
        for i, s in enumerate(sorted(rng.uniform(0, 600) for _ in range(120)))
    ]  # long turns overlap many later ones
    words = [
        TimedWord(start=s, end=s + rng.uniform(0.05, 0.6), text="w")
        for s in sorted(rng.uniform(-5, 640) for _ in range(500))
    ]

    assert _speakers(words, turns) == [_brute_force(word, turns) for word in words]
