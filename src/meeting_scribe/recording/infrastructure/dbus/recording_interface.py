"""``dev.meetingscribe.Recording1``: thin D-Bus adapter over ``RecordingController``."""

import asyncio
import contextlib
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
    """The controller is neither thread-safe nor non-blocking (startup grace, bounded stop wait).

    The stop wait is derived from ``RECORDING_STOP_TIMEOUT_SECONDS``.

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
        self._monitor_halt = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None
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
        """Poll the controller so a capture crash is announced without a client call.

        A failing poll is logged and the loop keeps going: one unreadable log must not end
        crash detection for the rest of the process. The loop only ends on ``halt_monitor``
        (after its current iteration) or cancellation.
        """
        while not self._monitor_halt.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._monitor_halt.wait(), timeout=self._poll_interval)
            if self._monitor_halt.is_set():
                return
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Crash monitor poll failed; will retry.")

    async def _poll_once(self) -> None:
        if self._lock.locked():
            return  # a call is in flight and will publish its own outcome
        async with self._lock:
            status = await asyncio.to_thread(self._controller.status)
        self._publish(status)

    def start_monitor(self) -> None:
        self._monitor_halt.clear()
        self._monitor_task = asyncio.create_task(self.monitor())

    async def shutdown(self) -> Result[RecordingSession, MeetingScribeError] | None:
        """Stop the monitor without cancelling it, then stop an active recording.

        Cancelling the monitor mid ``to_thread`` would leave its worker thread running while
        shutdown starts another one, so the monitor is asked to finish its iteration instead.
        """
        self._monitor_halt.set()
        if self._monitor_task is not None:
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        return await self.stop_if_active()

    async def stop_if_active(self) -> Result[RecordingSession, MeetingScribeError] | None:
        """Shutdown hook: ffmpeg runs in its own session and would otherwise be orphaned.

        Returns the stop outcome, or ``None`` when nothing was recording. A failure is logged
        at ERROR with its code: it may mean an orphaned ffmpeg or a truncated final chunk.
        """
        outcome: Result[RecordingSession, MeetingScribeError] | None = None
        async with self._lock:
            status = await asyncio.to_thread(self._controller.status)
            if status.state == "RECORDING":
                outcome = await asyncio.to_thread(self._controller.stop)
                status = await asyncio.to_thread(self._controller.status)
        if isinstance(outcome, Failure):
            error = outcome.failure()
            logger.error("Shutdown could not stop the recording: [%s] %s", error.error_code, error)
        self._publish(status)
        return outcome

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
        try:
            self.StateChanged(status.state, status.session_id)
            self.emit_properties_changed(
                {
                    "State": status.state,
                    "SessionId": status.session_id,
                    "Directory": status.directory,
                    "FailureReason": status.failure_reason,
                }
            )
        except Exception:  # e.g. the bus is gone: the state is still recorded locally
            logger.exception("Could not announce the state change on the bus.")
