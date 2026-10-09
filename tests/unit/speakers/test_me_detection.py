import pytest

from meeting_scribe.speakers.domain.me_detection import (
    BLEED_MIN_ACTIVE_SECONDS,
    choose_diarization_channel,
    detect_me,
    estimate_bleed,
    merge_turns,
)
from meeting_scribe.speakers.domain.params import MeParams
from meeting_scribe.speakers.domain.turns import ME, UNKNOWN, Turn
from tests.unit.speakers.envelopes import FLOOR, SPEECH, attenuated, envelope

PARAMS = MeParams(dominance_db=6.0, noise_margin_db=10.0, min_turn_seconds=0.5)


def _spans(turns) -> list[tuple[float, float]]:
    return [(round(t.start, 3), round(t.end, 3)) for t in turns]


def test_mic_only_speech_is_me() -> None:
    mic = envelope(60, [(10, 13, SPEECH)])
    system = envelope(60)

    found = detect_me(mic, system, system_turns=[], params=PARAMS)

    assert _spans(found.me_turns) == [(10.0, 13.0)]
    assert {turn.speaker for turn in found.me_turns} == {ME}
    assert found.unknown_turns == ()
    assert found.bleed == 0.0


def test_bleed_is_not_me_and_double_talk_is() -> None:
    bleed = SPEECH * attenuated(15)
    system = envelope(120, [(20, 60, SPEECH), (70, 100, SPEECH)])
    mic = envelope(120, [(20, 60, bleed), (70, 100, bleed), (80, 83, 5e-3)])
    system_turns = [Turn(start=20, end=60, speaker="S1"), Turn(start=70, end=100, speaker="S2")]

    found = detect_me(mic, system, system_turns, PARAMS)

    assert _spans(found.me_turns) == [(80.0, 83.0)]  # nothing for the 15 dB bleed
    assert found.unknown_turns == ()
    assert found.bleed == pytest.approx(0.0317, abs=1e-3)


def test_speech_that_is_neither_dominant_nor_a_system_turn_is_unknown() -> None:
    bleed = SPEECH * attenuated(15)
    system = envelope(120, [(20, 60, SPEECH), (70, 100, SPEECH)])
    # 3e-4 of residual is speech-level (above the noise margin) but under 6 dB over the bleed
    mic = envelope(120, [(20, 60, bleed), (70, 100, bleed), (80, 83, 3e-4)])
    system_turns = [Turn(start=20, end=60, speaker="S1")]

    found = detect_me(mic, system, system_turns, PARAMS)

    assert found.me_turns == ()
    assert [(t.speaker, *_spans([t])[0]) for t in found.unknown_turns] == [(UNKNOWN, 80.0, 83.0)]


def test_ambiguous_speech_under_a_system_turn_is_not_unknown() -> None:
    bleed = SPEECH * attenuated(15)
    system = envelope(120, [(20, 60, SPEECH), (70, 100, SPEECH)])
    mic = envelope(120, [(20, 60, bleed), (70, 100, bleed), (80, 83, 3e-4)])
    system_turns = [Turn(start=70, end=100, speaker="S2")]

    found = detect_me(mic, system, system_turns, PARAMS)

    assert found.me_turns == () and found.unknown_turns == ()


def test_short_bursts_are_dropped_and_close_ones_merge() -> None:
    mic = envelope(
        60,
        [
            (5.0, 5.4, SPEECH),  # 0.4 s: shorter than the minimum
            (10.0, 10.4, SPEECH),
            (10.6, 11.0, SPEECH),  # 0.2 s gap: one turn of 1.0 s
            (20.0, 20.6, SPEECH),
            (20.9, 21.5, SPEECH),  # 0.3 s gap: still one turn
            (30.0, 30.6, SPEECH),
            (31.0, 31.6, SPEECH),  # 0.4 s gap: two turns
        ],
    )

    found = detect_me(mic, envelope(60), [], PARAMS)

    assert _spans(found.me_turns) == [
        (10.0, 11.0),
        (20.0, 21.5),
        (30.0, 30.6),
        (31.0, 31.6),
    ]


def test_the_thresholds_are_the_parameters() -> None:
    mic = envelope(60, [(10, 13, SPEECH)])
    system = envelope(60)

    strict = MeParams(dominance_db=60.0, noise_margin_db=10.0, min_turn_seconds=0.5)
    long_only = MeParams(dominance_db=6.0, noise_margin_db=10.0, min_turn_seconds=5.0)

    assert detect_me(mic, system, [], strict).me_turns == ()  # 40 dB over the floor is not 60
    assert detect_me(mic, system, [], long_only).me_turns == ()


def test_a_silent_or_missing_microphone_gives_no_me_and_no_unknown() -> None:
    found = detect_me([0.0] * 600, envelope(60, [(10, 20, SPEECH)]), [], PARAMS)

    assert found.me_turns == () and found.unknown_turns == ()
    assert detect_me([], [], [], PARAMS).me_turns == ()


def test_channels_of_different_length_use_the_shorter_one() -> None:
    mic = envelope(60, [(10, 13, SPEECH), (59, 60, SPEECH)])

    found = detect_me(mic, envelope(30), [], PARAMS)

    assert _spans(found.me_turns) == [(10.0, 13.0)]


def test_bleed_needs_enough_active_system_audio_and_is_clamped() -> None:
    quiet = envelope(60, [(10, 20, SPEECH)])  # 10 s of system audio: not enough to estimate
    leaky_mic = envelope(60, [(10, 20, SPEECH * 0.5)])
    assert BLEED_MIN_ACTIVE_SECONDS == 30.0
    assert estimate_bleed(leaky_mic, quiet) == 0.0

    system = envelope(100, [(10, 60, SPEECH)])
    assert estimate_bleed(envelope(100, [(10, 60, SPEECH * 4)]), system) == 1.0
    mic = envelope(100, [(10, 60, SPEECH * 0.1)])
    assert estimate_bleed(mic, system) == pytest.approx(0.1, rel=0.01)


def test_me_wins_over_a_system_turn_and_the_merged_turns_are_exclusive() -> None:
    system = [Turn(start=0, end=10, speaker="S1"), Turn(start=12, end=20, speaker="S2")]
    me = [Turn(start=4, end=6, speaker=ME), Turn(start=9, end=13, speaker=ME)]

    merged = merge_turns(system, me)

    assert [(t.speaker, t.start, t.end) for t in merged] == [
        ("S1", 0, 4),
        (ME, 4, 6),
        ("S1", 6, 9),
        (ME, 9, 13),
        ("S2", 13, 20),
    ]


def test_a_system_turn_covered_by_me_disappears_and_unknown_is_cut_too() -> None:
    merged = merge_turns(
        [Turn(start=5, end=6, speaker="S1")],
        [Turn(start=4, end=7, speaker=ME)],
        unknown=[Turn(start=6.5, end=9, speaker=UNKNOWN)],
    )

    assert [(t.speaker, t.start, t.end) for t in merged] == [(ME, 4, 7), (UNKNOWN, 7, 9)]


def test_a_silent_system_channel_means_an_in_person_meeting_and_the_mic_is_diarized() -> None:
    assert choose_diarization_channel(envelope(600)) == "mic"
    assert choose_diarization_channel([0.0] * 600) == "mic"


def test_the_one_percent_threshold_of_system_speech() -> None:
    assert choose_diarization_channel(envelope(600, [(10, 15.9, SPEECH)])) == "mic"  # 0.98 %
    assert choose_diarization_channel(envelope(600, [(10, 16.1, SPEECH)])) == "system"  # 1.02 %
    assert choose_diarization_channel(envelope(600, [(10, 300, SPEECH)])) == "system"


def test_there_is_nothing_to_decide_without_system_audio_frames() -> None:
    assert choose_diarization_channel([]) == "system"


def test_a_noise_floor_alone_never_counts_as_system_speech() -> None:
    assert choose_diarization_channel(envelope(600, floor=1e-5)) == "mic"
    assert FLOOR < 1e-5
