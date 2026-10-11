"""Tells a service that went away from one that was never there.

At start-up the bus reports a missing name at once; the first call then wakes the service (bus
activation) or fails with a "not running" error that the window shows with a Retry button. Only a
disappearance after an appearance is a change worth announcing.
"""


class AvailabilityFilter:
    def __init__(self) -> None:
        self._seen = False

    def changed(self, *, available: bool) -> bool | None:
        """The availability to announce, or ``None`` to stay quiet."""
        if available:
            self._seen = True
            return True
        return False if self._seen else None
