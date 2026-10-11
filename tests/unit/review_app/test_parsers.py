"""Parsers turn the plain values of the Meetings1 replies into models (AC-1, AC-8, AC-13)."""

from datetime import UTC, datetime

from meeting_scribe_review.core.errors import INVALID_REPLY
from meeting_scribe_review.core.models import MeetingSummary, Speaker, Utterance
from meeting_scribe_review.core.parsers import (
    parse_meeting_summaries,
    parse_speakers,
    parse_transcript,
)
from meeting_scribe_review.core.result import Err, Ok

SPEAKER_ROW = {
    "id": "S1",
    "label": "Speaker 1",
    "name": "Fake Name",
    "display": "Fake Name",
    "is_me": False,
    "talk_seconds": 12.5,
    "utterance_count": 3,
    "has_sample": True,
    "merged_into": "",
}


def test_parse_meeting_summaries() -> None:
    rows = [
        ("m1", "2026-10-09T10:15:00+00:00", 61.5, "DONE", 2),
        ("m0", "2026-10-08T09:00:00", 0.0, "QUEUED", 0),
    ]
    result = parse_meeting_summaries(rows)
    assert result == Ok(
        [
            MeetingSummary(
                id="m1",
                started_at=datetime(2026, 10, 9, 10, 15, tzinfo=UTC),
                duration_seconds=61.5,
                state="DONE",
                active_speaker_count=2,
            ),
            MeetingSummary(
                id="m0",
                started_at=datetime(2026, 10, 8, 9, 0).astimezone(UTC),
                duration_seconds=0.0,
                state="QUEUED",
                active_speaker_count=0,
            ),
        ]
    )


def test_timestamps_are_normalised_to_utc_so_they_always_compare() -> None:
    result = parse_meeting_summaries(
        [
            ("a", "2026-10-09T12:00:00+02:00", 1.0, "DONE", 1),
            ("b", "2026-10-09T09:30:00", 1.0, "DONE", 1),  # naive: local time
            ("c", "2026-10-09T09:00:00+00:00", 1.0, "DONE", 1),
        ]
    )
    assert isinstance(result, Ok)
    assert all(m.started_at.utcoffset() == UTC.utcoffset(None) for m in result.value)
    assert result.value[0].started_at == datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    assert result.value[1].started_at == datetime(2026, 10, 9, 9, 30).astimezone(UTC)
    assert len(sorted(result.value, key=lambda m: m.started_at)) == 3


def test_parse_meeting_summaries_rejects_a_bad_date() -> None:
    result = parse_meeting_summaries([("m1", "not a date", 1.0, "DONE", 1)])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_parse_meeting_summaries_rejects_an_out_of_range_timestamp() -> None:
    # Year 1 at +14:00 is year 0 in UTC: astimezone raises OverflowError, not ValueError.
    result = parse_meeting_summaries([("m1", "0001-01-01T00:00:00+14:00", 1.0, "DONE", 1)])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_parse_meeting_summaries_rejects_a_short_row() -> None:
    result = parse_meeting_summaries([("m1", "2026-10-09T10:15:00", 1.0)])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_parse_speakers_reads_every_key() -> None:
    merged = {
        **SPEAKER_ROW,
        "id": "S2",
        "label": "Speaker 2",
        "name": "",
        "display": "Speaker 2",
        "has_sample": False,
        "merged_into": "S1",
    }
    result = parse_speakers([SPEAKER_ROW, merged])
    assert result == Ok(
        [
            Speaker("S1", "Speaker 1", "Fake Name", "Fake Name", False, 12.5, 3, True, ""),
            Speaker("S2", "Speaker 2", "", "Speaker 2", False, 12.5, 3, False, "S1"),
        ]
    )
    speakers = result.value if isinstance(result, Ok) else []
    assert speakers[0].is_active and not speakers[1].is_active


def test_parse_speakers_accepts_an_empty_list() -> None:
    assert parse_speakers([]) == Ok([])


def test_parse_speakers_rejects_a_missing_key() -> None:
    row = {k: v for k, v in SPEAKER_ROW.items() if k != "display"}
    result = parse_speakers([row])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_parse_speakers_rejects_a_wrongly_typed_value() -> None:
    result = parse_speakers([{**SPEAKER_ROW, "has_sample": "yes"}])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_parse_speakers_does_not_take_a_bool_for_a_count() -> None:
    result = parse_speakers([{**SPEAKER_ROW, "utterance_count": True}])
    assert isinstance(result, Err)


def test_parse_transcript() -> None:
    result = parse_transcript(
        [("S1", 0.0, 2.0, "Fake line one."), ("S2", 2.0, 4.5, "Fake line two.")]
    )
    assert result == Ok(
        [Utterance("S1", 0.0, 2.0, "Fake line one."), Utterance("S2", 2.0, 4.5, "Fake line two.")]
    )


def test_parse_transcript_of_a_meeting_without_speech_is_empty() -> None:
    assert parse_transcript([]) == Ok([])


def test_parse_transcript_rejects_a_malformed_row() -> None:
    result = parse_transcript([("S1", "zero", 2.0, "Fake line.")])
    assert isinstance(result, Err) and result.error.code == INVALID_REPLY


def test_an_error_never_quotes_the_reply() -> None:
    result = parse_speakers([{**SPEAKER_ROW, "name": 7, "display": "Private Fake"}])
    assert isinstance(result, Err)
    assert "Private Fake" not in repr(result.error)
