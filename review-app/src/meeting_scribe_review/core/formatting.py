"""Display helpers for times. Locale-aware dates are the view's job (it owns GLib)."""

from datetime import datetime

_MINUTE = 60
_HOUR = 3600


def format_timestamp(seconds: float) -> str:
    """``mm:ss``, or ``h:mm:ss`` from one hour on."""
    total = max(int(seconds), 0)
    hours, rest = divmod(total, _HOUR)
    minutes, secs = divmod(rest, _MINUTE)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """``45 s``, ``12 min`` or ``1 h 05 min``."""
    total = max(int(seconds), 0)
    if total < _MINUTE:
        return f"{total} s"
    if total < _HOUR:
        return f"{total // _MINUTE} min"
    hours, rest = divmod(total, _HOUR)
    return f"{hours} h {rest // _MINUTE:02d} min"


def format_speaker_count(count: int) -> str:
    return f"{count} speaker" if count == 1 else f"{count} speakers"


def unix_seconds(moment: datetime) -> int | None:
    """Whole Unix seconds of ``moment``, or ``None`` when it cannot be converted.

    An extreme or malformed value must never break the meeting list: the view shows "Unknown date".
    """
    try:
        return int(moment.timestamp())
    except (OverflowError, OSError, ValueError, AttributeError, TypeError):
        return None
