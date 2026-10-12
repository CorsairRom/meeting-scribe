"""Fan-out of the summary events to several observers, and the progress throttle (spec 10)."""

import logging
import threading
import time
from collections.abc import Callable

from meeting_scribe.meetings.domain.ports import SummaryEvents, SummaryOutcome

logger = logging.getLogger(__name__)


class SummaryEventHub:
    """A ``SummaryEvents`` that tells every subscriber; one broken subscriber hears the rest.

    The D-Bus interface exists only once the bus is connected, so it subscribes later. Events
    arrive on the summary job thread. Nothing here logs an argument.
    """

    def __init__(self) -> None:
        self._observers: tuple[SummaryEvents, ...] = ()

    def subscribe(self, observer: SummaryEvents) -> None:
        self._observers = (*self._observers, observer)

    def unsubscribe(self, observer: SummaryEvents) -> None:
        self._observers = tuple(o for o in self._observers if o is not observer)

    def progress(self, meeting_id: str, step: int, total: int) -> None:
        for observer in self._observers:
            self._tell(observer.progress, meeting_id, step, total)

    def finished(self, meeting_id: str, state: SummaryOutcome, error_code: str) -> None:
        for observer in self._observers:
            self._tell(observer.finished, meeting_id, state, error_code)

    def active_changed(self) -> None:
        for observer in self._observers:
            self._tell(observer.active_changed)

    @staticmethod
    def _tell(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:
            logger.warning("A summary observer failed.", exc_info=True)


class ProgressThrottle:
    """At most one progress signal per interval; the last step of a run is always let through."""

    def __init__(
        self, interval_seconds: float, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._interval = interval_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last: float | None = None

    def allow(self, step: int, total: int) -> bool:
        now = self._monotonic()
        with self._lock:
            if step < total and self._last is not None and now - self._last < self._interval:
                return False
            self._last = now
            return True

    def reset(self) -> None:
        with self._lock:
            self._last = None
