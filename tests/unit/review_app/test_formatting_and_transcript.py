"""Display helpers and the transcript rows shown with speaker names (AC-8)."""

from meeting_scribe_review.core.formatting import format_duration, format_timestamp
from meeting_scribe_review.core.models import Utterance
from meeting_scribe_review.core.transcript import TranscriptLine, build_transcript
from tests.unit.review_app.fakes import speaker


def test_timestamps_are_clock_style() -> None:
    assert format_timestamp(0.0) == "00:00"
    assert format_timestamp(2.9) == "00:02"
    assert format_timestamp(754.0) == "12:34"
    assert format_timestamp(3725.0) == "1:02:05"


def test_timestamps_never_go_negative() -> None:
    assert format_timestamp(-4.0) == "00:00"


def test_durations_are_short_and_readable() -> None:
    assert format_duration(0.0) == "0 s"
    assert format_duration(45.4) == "45 s"
    assert format_duration(60.0) == "1 min"
    assert format_duration(754.0) == "12 min"
    assert format_duration(3900.0) == "1 h 05 min"


def test_transcript_lines_carry_the_effective_display_name() -> None:
    speakers = [speaker("S1", name="Fake Alex"), speaker("S2")]
    lines = build_transcript(
        speakers, [Utterance("S1", 0.0, 2.0, "Fake one."), Utterance("S2", 62.0, 64.0, "Fake two.")]
    )
    assert lines == [
        TranscriptLine("S1", "Fake Alex", "00:00", "Fake one."),
        TranscriptLine("S2", "Speaker 2", "01:02", "Fake two."),
    ]


def test_an_unknown_speaker_id_falls_back_to_the_id() -> None:
    assert build_transcript([], [Utterance("S9", 0.0, 1.0, "Fake.")])[0].speaker_name == "S9"


def test_no_utterances_gives_no_lines() -> None:
    assert build_transcript([speaker("S1")], []) == []
