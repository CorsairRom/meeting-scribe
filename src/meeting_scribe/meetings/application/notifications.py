"""What the user is told when a job ends (spec 8, AC-18). Text only, never meeting content."""

import logging

from returns.result import Failure

from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.notification import Notification
from meeting_scribe.meetings.domain.ports import Notifier, NullProcessingEvents

logger = logging.getLogger(__name__)

_MAX_BODY = 200


def build_notification(meeting: Meeting) -> Notification:
    """``Meeting processed: N speakers`` or ``Meeting processing failed`` plus the reason."""
    failure = meeting.processing.failure
    if meeting.processing.state == "FAILED" and failure is not None:
        return Notification(summary="Meeting processing failed", body=_short(failure.message))
    count = len(meeting.speakers)
    if count == 0:
        outcome = "no speech detected"
    else:
        outcome = f"{count} speaker" if count == 1 else f"{count} speakers"
    cpu = any(
        label.startswith("cpu")
        for label in (
            meeting.processing.device.transcription,
            meeting.processing.device.diarization,
        )
    )
    return Notification(summary=f"Meeting processed: {outcome}{' (CPU)' if cpu else ''}")


def _short(message: str) -> str:
    """The first line of a failure message, cut to a notification-sized length."""
    lines = message.strip().splitlines()
    line = lines[0].strip() if lines else ""
    return line if len(line) <= _MAX_BODY else line[: _MAX_BODY - 3].rstrip() + "..."


class NotifyOnFinish(NullProcessingEvents):
    """Sends one notification per finished job; a failure is logged and nothing else (AC-18)."""

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    def finished(self, meeting: Meeting) -> None:
        sent = self._notifier.notify(build_notification(meeting))
        if isinstance(sent, Failure):
            logger.warning(
                "Desktop notification for meeting '%s' failed: [%s] %s",
                meeting.id,
                sent.failure().error_code,
                sent.failure(),
            )
