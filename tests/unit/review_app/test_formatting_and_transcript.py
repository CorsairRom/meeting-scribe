"""Display helpers and the transcript rows shown with speaker names (AC-8)."""

from datetime import UTC, datetime

import pytest

from meeting_scribe_review.core.formatting import (
    format_duration,
    format_speaker_count,
    format_timestamp,
    unix_seconds,
)
from meeting_scribe_review.core.models import Utterance
from meeting_scribe_review.core.transcript import TranscriptLine, build_transcript
from tests.unit.review_app.fakes import speaker

EXPECTED_NOON = int(datetime(2026, 10, 11, 12, 0, tzinfo=UTC).timestamp())


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


def test_speaker_counts_are_pluralised() -> None:
    assert format_speaker_count(0) == "0 speakers"
    assert format_speaker_count(1) == "1 speaker"
    assert format_speaker_count(3) == "3 speakers"


def test_unix_seconds_of_a_normal_moment() -> None:
    assert unix_seconds(datetime(2026, 10, 11, 12, 0, tzinfo=UTC)) == EXPECTED_NOON


class _Unconvertible:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def timestamp(self) -> float:
        raise self._error


@pytest.mark.parametrize("error", [OverflowError(), OSError(), ValueError()])
def test_unix_seconds_gives_none_when_the_conversion_fails(error: Exception) -> None:
    assert unix_seconds(_Unconvertible(error)) is None  # type: ignore[arg-type]


def test_unix_seconds_gives_none_for_a_malformed_value() -> None:
    assert unix_seconds("not a date") is None  # type: ignore[arg-type]
