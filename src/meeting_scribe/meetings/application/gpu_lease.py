"""The single-GPU rule as one in-memory lease shared by recording, processing and summaries.

Spec ``summaries.md`` section 7 (ADR-003, ADR-007): a recording is never delayed, so it is
always granted and it revokes the others; a worker or a local summary is granted only when
nobody holds the lease.
"""

import logging
import threading
from collections.abc import Callable

from meeting_scribe.meetings.domain.ports import GpuHolder

logger = logging.getLogger(__name__)


class InMemoryGpuLease:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holders: dict[GpuHolder, Callable[[], None] | None] = {}

    def available(self, holder: GpuHolder) -> bool:
        with self._lock:
            return holder == "recording" or not self._holders

    def acquire(self, holder: GpuHolder, on_revoke: Callable[[], None] | None = None) -> bool:
        with self._lock:
            if holder == "recording":
                revoked = [
                    callback
                    for other, callback in self._holders.items()
                    if other != "recording" and callback is not None
                ]
                for other in self._holders:  # a holder is revoked once
                    if other != "recording":
                        self._holders[other] = None
                self._holders.setdefault("recording", on_revoke)
            elif self._holders:
                return False
            else:
                self._holders[holder] = on_revoke
                return True
        for callback in revoked:  # outside the lock: a callback may take its own locks
            try:
                callback()
            except Exception:
                logger.exception("A GPU lease revoke callback failed.")
        return True

    def release(self, holder: GpuHolder) -> None:
        with self._lock:
            self._holders.pop(holder, None)
