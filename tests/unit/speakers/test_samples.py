import pytest
from pydantic import ValidationError

from meeting_scribe.speakers.domain.params import SampleParams
from meeting_scribe.speakers.domain.samples import (
    Clip,
    clean_turns_of,
    me_turns_with_quiet_system,
    select_sample_clips,
)
from meeting_scribe.speakers.domain.turns import ME, Turn
from tests.unit.speakers.envelopes import SPEECH, envelope

PARAMS = SampleParams(max_clips=3, target_seconds=15.0, min_clip_seconds=1.5, max_clip_seconds=6.0)


def _turns(*lengths: float, speaker: str = "S1") -> list[Turn]:
    out, at = [], 0.0
    for length in lengths:
        out.append(Turn(start=at, end=at + length, speaker=speaker))
        at += length + 1.0
    return out


def _lengths(clips: tuple[Clip, ...]) -> list[float]:
    return [round(clip.end - clip.start, 3) for clip in clips]


def test_longest_clean_turns_are_selected() -> None:
    turns = _turns(2, 9, 4, 1)  # only clean turns are passed in (see the clean-turn test)

    clips = select_sample_clips(turns, PARAMS)

    assert _lengths(clips) == [6.0, 4.0, 2.0]  # the 9 s turn is trimmed to its first 6 s
    assert (clips[0].start, clips[0].end) == (3.0, 9.0)  # trimmed from its start


def test_the_last_clip_is_shortened_so_the_total_never_exceeds_the_target() -> None:
    params = PARAMS.model_copy(update={"max_clips": 5})

    assert _lengths(select_sample_clips(_turns(10, 10, 10), params)) == [6.0, 6.0, 3.0]


def test_a_remainder_shorter_than_the_minimum_clip_ends_the_selection() -> None:
    params = PARAMS.model_copy(update={"target_seconds": 13.0})

    assert _lengths(select_sample_clips(_turns(10, 10, 10), params)) == [6.0, 6.0]


def test_at_most_max_clips_are_taken() -> None:
    params = PARAMS.model_copy(update={"max_clips": 2})

    assert len(select_sample_clips(_turns(5, 5, 5, 5), params)) == 2


def test_turns_shorter_than_the_minimum_give_no_sample() -> None:
    assert select_sample_clips(_turns(1.4, 1.0), PARAMS) == ()
    assert select_sample_clips([], PARAMS) == ()


def test_equally_long_turns_are_taken_earliest_first() -> None:
    params = PARAMS.model_copy(update={"max_clips": 1})
    turns = [Turn(start=30, end=34, speaker="S1"), Turn(start=10, end=14, speaker="S1")]

    assert select_sample_clips(turns, params)[0].start == 10


def test_a_clean_turn_overlaps_no_other_speaker() -> None:
    turns = [
        Turn(start=0, end=6, speaker="S1"),  # overlapped by S2
        Turn(start=5, end=8, speaker="S2"),
        Turn(start=10, end=12, speaker="S1"),
        Turn(start=12, end=15, speaker="S2"),  # touches S1 at 12 only: no overlap
        Turn(start=20, end=26, speaker="S1"),
        Turn(start=21, end=22, speaker="S1"),  # same speaker: not another speaker
    ]

    assert [(t.start, t.end) for t in clean_turns_of("S1", turns)] == [
        (10, 12),
        (20, 26),
        (21, 22),
    ]


def test_me_turns_count_only_while_the_system_channel_is_quiet() -> None:
    system = envelope(60, [(20, 30, SPEECH)])
    me = [
        Turn(start=5, end=10, speaker=ME),  # quiet system
        Turn(start=18, end=22, speaker=ME),  # the system starts playing at 20 s
        Turn(start=40, end=45, speaker=ME),
    ]

    quiet = me_turns_with_quiet_system(me, system, 10.0)

    assert [(t.start, t.end) for t in quiet] == [(5, 10), (40, 45)]


def test_frames_the_system_envelope_does_not_have_are_not_evidence_of_quiet() -> None:
    system = envelope(10)  # 10 s long, silent
    me = [
        Turn(start=2, end=5, speaker=ME),  # fully covered
        Turn(start=8, end=12, speaker=ME),  # runs past the end of the envelope
        Turn(start=20, end=25, speaker=ME),  # entirely past it
    ]

    assert [(t.start, t.end) for t in me_turns_with_quiet_system(me, system, 10.0)] == [(2, 5)]
    assert me_turns_with_quiet_system(me, [], 10.0) == ()


def test_the_quiet_check_follows_the_noise_margin() -> None:
    system = envelope(60, [(20, 30, SPEECH / 100)])  # 20 dB over the floor
    me = [Turn(start=18, end=22, speaker=ME)]

    assert me_turns_with_quiet_system(me, system, 10.0) == ()
    assert len(me_turns_with_quiet_system(me, system, 30.0)) == 1


def test_sample_params_reject_contradictory_bounds() -> None:
    with pytest.raises(ValidationError):
        SampleParams(max_clips=3, target_seconds=15.0, min_clip_seconds=7.0, max_clip_seconds=6.0)
    with pytest.raises(ValidationError):
        SampleParams(max_clips=3, target_seconds=1.0, min_clip_seconds=1.5, max_clip_seconds=6.0)
