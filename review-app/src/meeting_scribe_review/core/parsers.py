"""Plain D-Bus values (as unpacked by the transport) to models.

A malformed reply becomes ``Err(ServiceError(INVALID_REPLY))``. The error never carries the reply,
which may hold private names or transcript text.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.models import (
    FieldIssue,
    MeetingSummary,
    ProviderRow,
    SetOutcome,
    SettingsField,
    Speaker,
    SummaryDocument,
    SummaryInfo,
    Utterance,
)
from meeting_scribe_review.core.result import Err, Ok, Result

_INVALID = Err(ServiceError(INVALID_REPLY))
_SUMMARY_FIELDS = 5
_UTTERANCE_FIELDS = 4
_ISSUE_FIELDS = 3


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
        # The service sends aware timestamps; a naive one is read as local time. Always UTC, so
        # moments compare and sort without a naive/aware TypeError. An extreme offset can leave
        # the representable range (OverflowError).
        return moment.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise _InvalidReplyError from error


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


def _provider(row: Any) -> ProviderRow:
    fields = _mapping(row)
    return ProviderRow(
        id=_text(_key(fields, "id")),
        label=_text(_key(fields, "label")),
        cloud=_flag(_key(fields, "cloud")),
        model=_text(_key(fields, "model")),
        available=_flag(_key(fields, "available")),
        reason=_text(_key(fields, "reason")),
        default=_flag(_key(fields, "default")),
    )


def _info(row: Any) -> SummaryInfo:
    fields = _mapping(row)
    generated_at = _text(_key(fields, "generated_at"))
    return SummaryInfo(
        state=_text(_key(fields, "state")),
        stale=_flag(_key(fields, "stale")),
        cloud=_flag(_key(fields, "cloud")),
        provider_label=_text(_key(fields, "provider_label")),
        generated_at=_moment(generated_at) if generated_at else None,
        step=_count(_key(fields, "step")),
        total_steps=_count(_key(fields, "total_steps")),
        failure_code=_text(_key(fields, "failure_code")),
    )


def _document(row: Any) -> SummaryDocument:
    fields = _mapping(row)
    return SummaryDocument(
        markdown=_text(_key(fields, "markdown")),
        title=_text(_key(fields, "title")),
        generated_at=_moment(_key(fields, "generated_at")),
        provider_label=_text(_key(fields, "provider_label")),
        cloud=_flag(_key(fields, "cloud")),
        model=_text(_key(fields, "model")),
        language=_text(_key(fields, "language")),
        stale=_flag(_key(fields, "stale")),
    )


def _parse_one[T](row: Any, one: Callable[[Any], T]) -> Result[T, ServiceError]:
    try:
        return Ok(one(row))
    except _InvalidReplyError:
        return _INVALID


def parse_providers(rows: Any) -> Result[list[ProviderRow], ServiceError]:
    """``Providers``: ``aa{sv}``."""
    return _parse_all(rows, _provider)


def parse_summary_info(row: Any) -> Result[SummaryInfo, ServiceError]:
    """``GetSummaryStatus``: ``a{sv}``."""
    return _parse_one(row, _info)


def parse_summary_document(row: Any) -> Result[SummaryDocument, ServiceError]:
    """``GetSummary``: ``a{sv}``."""
    return _parse_one(row, _document)


def parse_started(value: Any) -> Result[str, ServiceError]:
    """``Summarize``: ``s`` (``RUNNING``)."""
    return _parse_one(value, _text)


def parse_cancelled(value: Any) -> Result[bool, ServiceError]:
    """``CancelSummary``: ``b``."""
    return _parse_one(value, _flag)


def _texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise _InvalidReplyError
    return tuple(_text(item) for item in value)


def _setting(row: Any) -> SettingsField:
    fields = _mapping(row)
    return SettingsField(
        key=_text(_key(fields, "key")),
        kind=_text(_key(fields, "kind")),
        value=_text(_key(fields, "value")),
        default=_text(_key(fields, "default")),
        source=_text(_key(fields, "source")),
        locked=_flag(_key(fields, "locked")),
        lock_reason=_text(_key(fields, "lock_reason")),
        apply=_text(_key(fields, "apply")),
        choices=_texts(_key(fields, "choices")),
        file_problem=_text(_key(fields, "file_problem")),
        apply_problem=_text(_key(fields, "apply_problem")),
    )


def _issue(row: Any) -> FieldIssue:
    key, code, message = _tuple(row, _ISSUE_FIELDS)
    return FieldIssue(_text(key), _text(code), _text(message))


def _outcome(row: Any) -> SetOutcome:
    fields = _mapping(row)
    errors = _key(fields, "errors")
    if isinstance(errors, str) or not isinstance(errors, Sequence):
        raise _InvalidReplyError
    return SetOutcome(
        applied=_texts(_key(fields, "applied")),
        restart_required=_texts(_key(fields, "restart_required")),
        apply_failed=_flag(_key(fields, "apply_failed")),
        errors=tuple(_issue(error) for error in errors),
    )


def parse_settings_fields(rows: Any) -> Result[list[SettingsField], ServiceError]:
    """``Settings1.GetAll``: ``aa{sv}``."""
    return _parse_all(rows, _setting)


def parse_set_outcome(row: Any) -> Result[SetOutcome, ServiceError]:
    """``Settings1.Set``: ``a{sv}``."""
    return _parse_one(row, _outcome)


def parse_keys(value: Any) -> Result[tuple[str, ...], ServiceError]:
    """``Settings1.RestartRequired`` (read through ``Properties.Get``): ``as``."""
    return _parse_one(value, _texts)


def parse_restarting(value: Any) -> Result[str, ServiceError]:
    """``Settings1.Restart``: ``s`` (``RESTARTING``)."""
    return _parse_one(value, _text)
