"""Plain D-Bus values (as unpacked by the transport) to models.

A malformed reply becomes ``Err(ServiceError(INVALID_REPLY))``. The error never carries the reply,
which may hold private names or transcript text.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.models import MeetingSummary, Speaker, Utterance
from meeting_scribe_review.core.result import Err, Ok, Result

_INVALID = Err(ServiceError(INVALID_REPLY))
_SUMMARY_FIELDS = 5
_UTTERANCE_FIELDS = 4


class _InvalidReplyError(Exception):
    """Internal: raised by the field readers, turned into ``Err`` at each public function."""


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise _InvalidReplyError
    return value


def _flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _InvalidReplyError
    return value


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidReplyError
    return value


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _InvalidReplyError
    return float(value)


def _moment(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(_text(value))
    except ValueError as error:
        raise _InvalidReplyError from error
    # The service sends aware timestamps; a naive one is read as local time. Always UTC, so
    # moments compare and sort without a naive/aware TypeError.
    return moment.astimezone(UTC)


def _tuple(row: Any, size: int) -> Sequence[Any]:
    if isinstance(row, str) or not isinstance(row, Sequence) or len(row) != size:
        raise _InvalidReplyError
    return row


def _mapping(row: Any) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        raise _InvalidReplyError
    return row


def _key(row: Mapping[str, Any], name: str) -> Any:
    if name not in row:
        raise _InvalidReplyError
    return row[name]


def _summary(row: Any) -> MeetingSummary:
    meeting_id, started_at, duration, state, speakers = _tuple(row, _SUMMARY_FIELDS)
    return MeetingSummary(
        id=_text(meeting_id),
        started_at=_moment(started_at),
        duration_seconds=_seconds(duration),
        state=_text(state),
        active_speaker_count=_count(speakers),
    )


def _speaker(row: Any) -> Speaker:
    fields = _mapping(row)
    return Speaker(
        id=_text(_key(fields, "id")),
        label=_text(_key(fields, "label")),
        name=_text(_key(fields, "name")),
        display=_text(_key(fields, "display")),
        is_me=_flag(_key(fields, "is_me")),
        talk_seconds=_seconds(_key(fields, "talk_seconds")),
        utterance_count=_count(_key(fields, "utterance_count")),
        has_sample=_flag(_key(fields, "has_sample")),
        merged_into=_text(_key(fields, "merged_into")),
    )


def _utterance(row: Any) -> Utterance:
    speaker_id, start, end, text = _tuple(row, _UTTERANCE_FIELDS)
    return Utterance(_text(speaker_id), _seconds(start), _seconds(end), _text(text))


def _parse_all[T](rows: Any, one: Callable[[Any], T]) -> Result[list[T], ServiceError]:
    if isinstance(rows, str) or not isinstance(rows, Sequence):
        return _INVALID
    try:
        return Ok([one(row) for row in rows])
    except _InvalidReplyError:
        return _INVALID


def parse_meeting_summaries(rows: Any) -> Result[list[MeetingSummary], ServiceError]:
    """``List``: ``a(ssdsi)``."""
    return _parse_all(rows, _summary)


def parse_speakers(rows: Any) -> Result[list[Speaker], ServiceError]:
    """``GetSpeakers``, ``RenameSpeaker``, ``MergeSpeakers``, ``UnmergeSpeaker``: ``aa{sv}``."""
    return _parse_all(rows, _speaker)


def parse_transcript(rows: Any) -> Result[list[Utterance], ServiceError]:
    """``GetTranscript``: ``a(sdds)``."""
    return _parse_all(rows, _utterance)
