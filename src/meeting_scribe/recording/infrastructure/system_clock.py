"""Wall-clock adapter."""

from datetime import datetime


class SystemClock:
    def now(self) -> datetime:
        """Local time, timezone-aware (session folder names use local time)."""
        return datetime.now().astimezone()
