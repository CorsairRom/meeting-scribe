"""Fan-out of the processing events to several observers (notifier, D-Bus interface)."""

import logging

from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.ports import ProcessingEvents

logger = logging.getLogger(__name__)


class ProcessingEventHub:
    """A ``ProcessingEvents`` that tells every subscriber; one broken subscriber hears the rest.

    Subscribers can be added after the controller is built (the D-Bus interface exists only once
    the bus is connected). Events arrive on the processing thread.
    """

    def __init__(self) -> None:
        self._observers: tuple[ProcessingEvents, ...] = ()

    def subscribe(self, observer: ProcessingEvents) -> None:
        self._observers = (*self._observers, observer)

    def unsubscribe(self, observer: ProcessingEvents) -> None:
        self._observers = tuple(o for o in self._observers if o is not observer)

    def state_changed(self, meeting: Meeting) -> None:
        for observer in self._observers:
            self._tell(observer.state_changed, meeting)

    def finished(self, meeting: Meeting) -> None:
        for observer in self._observers:
            self._tell(observer.finished, meeting)

    def storage_failed(self, meeting_id: str, error_code: str) -> None:
        for observer in self._observers:
            self._tell(observer.storage_failed, meeting_id, error_code)

    def status_changed(self) -> None:
        for observer in self._observers:
            self._tell(observer.status_changed)

    @staticmethod
    def _tell(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:
            logger.warning("A processing observer failed.", exc_info=True)
