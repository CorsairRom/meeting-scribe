"""``dev.meetingscribe.Recording1``: thin D-Bus adapter over ``RecordingController``."""

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated

from dbus_fast import PropertyAccess
from dbus_fast.annotations import DBusDouble, DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure, Result

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.recording.application.recording_controller import (
    RecordingController,
    RecordingStatus,
)
from meeting_scribe.recording.domain.recording_session import RecordingSession

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Recording1"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

DBusStatePair = Annotated[tuple[str, str], DBusSignature("ss")]


def _published(status: RecordingStatus) -> tuple[str, str, str, str]:
    """What clients are told about; elapsed time is excluded (it changes every second)."""
    return (status.state, status.session_id, status.directory, status.failure_reason)


class RecordingInterface(ServiceInterface):
    """The controller is neither thread-safe nor non-blocking (startup grace, stop up to 14 s).

    Every controller call therefore runs in a worker thread, one at a time under ``_lock``.
    While a call is in flight, property reads and the crash monitor use the last snapshot
    instead of touching the controller from the event-loop thread.
    """

    def __init__(
        self,
        controller: RecordingController,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(INTERFACE_NAME)
        self._controller = controller
        self._poll_interval = poll_interval
        self._lock = asyncio.Lock()
        self._snapshot = controller.status()
        self._last_published = _published(self._snapshot)

    @dbus_method()
    async def Start(self) -> DBusStr:
        return await self.start()

    @dbus_method()
    async def Stop(self) -> DBusStr:
        return await self.stop()

    # ``dbus_method`` wrappers discard the return value when called from Python (GO-022), so
    # the behavior lives in plain coroutines that tests and the shutdown hook can call.
    async def start(self) -> str:
        return await self._call(self._controller.start)

    async def stop(self) -> str:
        return await self._call(self._controller.stop)

    @dbus_property(PropertyAccess.READ)
    def State(self) -> DBusStr:
        return self._current().state

    @dbus_property(PropertyAccess.READ)
    def SessionId(self) -> DBusStr:
        return self._current().session_id

    @dbus_property(PropertyAccess.READ)
    def ElapsedSeconds(self) -> DBusDouble:
        return self._current().elapsed_seconds

    @dbus_property(PropertyAccess.READ)
    def Directory(self) -> DBusStr:
        return self._current().directory

    @dbus_property(PropertyAccess.READ)
    def FailureReason(self) -> DBusStr:
        return self._current().failure_reason

    @dbus_signal()
    def StateChanged(self, state: DBusStr, session_id: DBusStr) -> DBusStatePair:
        return (state, session_id)

    async def monitor(self) -> None:
        """Poll the controller so a capture crash is announced without a client call."""
        while True:
            await asyncio.sleep(self._poll_interval)
            if self._lock.locked():
                continue  # a call is in flight and will publish its own outcome
            async with self._lock:
                status = await asyncio.to_thread(self._controller.status)
            self._publish(status)

    async def stop_if_active(self) -> None:
        """Shutdown hook: ffmpeg runs in its own session and would otherwise be orphaned."""
        async with self._lock:
            status = await asyncio.to_thread(self._controller.status)
            if status.state == "RECORDING":
                await asyncio.to_thread(self._controller.stop)
                status = await asyncio.to_thread(self._controller.status)
        self._publish(status)

    async def _call(
        self, operation: Callable[[], Result[RecordingSession, MeetingScribeError]]
    ) -> str:
        async with self._lock:
            result, status = await asyncio.to_thread(self._run, operation)
        self._publish(status)
        if isinstance(result, Failure):
            raise to_dbus_error(result.failure())
        return result.unwrap().id

    def _run(
        self, operation: Callable[[], Result[RecordingSession, MeetingScribeError]]
    ) -> tuple[Result[RecordingSession, MeetingScribeError], RecordingStatus]:
        return operation(), self._controller.status()

    def _current(self) -> RecordingStatus:
        if self._lock.locked():
            return self._snapshot
        self._snapshot = self._controller.status()
        return self._snapshot

    def _publish(self, status: RecordingStatus) -> None:
        self._snapshot = status
        current = _published(status)
        if current == self._last_published:
            return
        self._last_published = current
        logger.info("Recording state is now %s.", status.state)
        self.StateChanged(status.state, status.session_id)
        self.emit_properties_changed(
            {
                "State": status.state,
                "SessionId": status.session_id,
                "Directory": status.directory,
                "FailureReason": status.failure_reason,
            }
        )
