"""Unpacked ``Meetings1`` signal arguments to listener calls; a malformed signal is dropped."""

from collections.abc import Sequence
from typing import Any

from meeting_scribe_review.core.gateway import GatewayListener, SummaryListener


def _text(value: Any) -> bool:
    return isinstance(value, str)


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shaped(args: Any, *checks: Any) -> bool:
    return (
        not isinstance(args, str)
        and isinstance(args, Sequence)
        and len(args) == len(checks)
        and all(check(value) for check, value in zip(checks, args, strict=True))
    )


def dispatch_signal(listener: GatewayListener, name: str, args: Any) -> bool:
    """Call the listener for a known signal; ``False`` when it is unknown or malformed."""
    if name == "ProcessingStateChanged" and _shaped(args, _text, _text):
        listener.on_processing_state_changed(args[0], args[1])
    elif name == "ProcessingFinished" and _shaped(args, _text, _text, _count):
        listener.on_processing_finished(args[0], args[1], args[2])
    elif name == "SpeakersChanged" and _shaped(args, _text):
        listener.on_speakers_changed(args[0])
    else:
        return False
    return True


def dispatch_summary_signal(listener: SummaryListener, name: str, args: Any) -> bool:
    """Call the listener for a known ``Summaries1`` signal; ``False`` when unknown or malformed."""
    if name == "SummaryProgress" and _shaped(args, _text, _count, _count):
        listener.on_summary_progress(args[0], args[1], args[2])
    elif name == "SummaryFinished" and _shaped(args, _text, _text, _text):
        listener.on_summary_finished(args[0], args[1], args[2])
    else:
        return False
    return True
