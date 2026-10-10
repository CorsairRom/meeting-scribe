from meeting_scribe.speakers.domain.turns import SpokenWord
from meeting_scribe.speakers.domain.utterances import group_utterances


def _word(start: float, text: str, speaker: str = "S1", length: float = 0.5) -> SpokenWord:
    return SpokenWord(start=start, end=start + length, text=text, speaker=speaker)


def test_gap_threshold_splits_utterances() -> None:
    first = _word(0.0, "uno")  # ends at 0.5
    second = _word(5.4, "dos")  # gap 4.9 s: same utterance; ends at 5.9
    third = _word(11.0, "tres")  # gap 5.1 s: new utterance

    utterances = group_utterances([first, second, third], max_gap=5.0)

    assert [u.text for u in utterances] == ["uno dos", "tres"]
    assert (utterances[0].start, utterances[0].end) == (0.0, 5.9)


def test_a_gap_of_exactly_the_maximum_still_joins() -> None:
    utterances = group_utterances([_word(0.0, "a"), _word(5.5, "b")], max_gap=5.0)

    assert [u.text for u in utterances] == ["a b"]


def test_a_change_of_speaker_always_starts_a_new_utterance() -> None:
    words = [_word(0.0, "a", "S1"), _word(0.5, "b", "S2"), _word(1.0, "c", "S1")]

    utterances = group_utterances(words, max_gap=5.0)

    assert [(u.speaker, u.text) for u in utterances] == [("S1", "a"), ("S2", "b"), ("S1", "c")]


def test_the_gap_is_configurable() -> None:
    words = [_word(0.0, "a"), _word(3.0, "b")]

    assert len(group_utterances(words, max_gap=2.0)) == 2
    assert len(group_utterances(words, max_gap=10.0)) == 1


def test_no_words_gives_no_utterances() -> None:
    assert group_utterances([], max_gap=5.0) == ()
