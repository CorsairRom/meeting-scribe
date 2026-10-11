"""Announces only a change of the service's availability.

The bus reports the current state of the name once at start-up (present or not); that is a
baseline, not news. A missing service at start is reported by the first call, which either wakes it
(bus activation) or fails with a "not running" error that the window shows with a Retry button.
"""


class AvailabilityFilter:
    def __init__(self) -> None:
        self._known: bool | None = None

    def changed(self, *, available: bool) -> bool | None:
        """The availability to announce, or ``None`` when nothing changed."""
        previous, self._known = self._known, available
        if previous is None or previous == available:
            return None
        return available
