"""Recording -> processing hand-off: the one place that knows both domains (spec section 1).

``recording`` cannot import ``meetings`` (and the reverse goes only through application layers),
so the composition root adapts the ``RecordingListener`` port to ``ProcessingController``.
Runs on the recording controller's worker thread: it only persists ``meeting.json`` and flips
the controller's flags, both quick.
"""

import logging

from returns.result import Failure

from meeting_scribe.meetings.application.processing_controller import ProcessingController
from meeting_scribe.meetings.domain.meeting import RecordingInfo
from meeting_scribe.recording.domain.recording_session import RecordingSession

logger = logging.getLogger(__name__)


class ProcessingRecordingListener:
    def __init__(self, processing: ProcessingController) -> None:
        self._processing = processing

    def on_started(self, session: RecordingSession) -> None:  # noqa: ARG002
        """No GPU work while recording (ADR-003, OQ-3): the running worker is stopped."""
        self._processing.preempt()

    def on_finished(self, session: RecordingSession) -> None:
        """Queue the meeting (a crashed capture too, OQ-1), then let the GPU work resume."""
        try:
            recording = RecordingInfo(
                started_at=session.started_at,
                stopped_at=session.stopped_at,
                state=session.state,
                failure_reason=session.failure_reason or "",
            )
            queued = self._processing.enqueue(session.id, recording)
            if isinstance(queued, Failure):
                error = queued.failure()
                logger.error(
                    "Meeting '%s' could not be queued for processing: [%s]. Its audio is kept "
                    "in its folder.",
                    session.id,
                    error.error_code,
                )
        finally:
            self._processing.release()  # GO-045: the GPU is released however the session ends
