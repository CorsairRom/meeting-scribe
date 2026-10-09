"""``org.freedesktop.Notifications.Notify`` over the session bus (spec 8).

Called from the processing thread, which has no event loop: each call runs its own short
``asyncio.run`` with a fresh connection, so it never touches the service's loop or bus.
Notifications are rare (one per finished job), so the connection cost does not matter.
"""

import asyncio

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus
from returns.result import Failure, Result, Success

from meeting_scribe.meetings.domain.errors import NotificationError
from meeting_scribe.meetings.domain.notification import Notification

APP_NAME = "Meeting Scribe"
_NAME = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"
_DEFAULT_TIMEOUT_SECONDS = 5.0


class FreedesktopNotifier:
    def __init__(
        self, bus_address: str | None = None, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._bus_address = bus_address
        self._timeout = timeout_seconds

    def notify(self, notification: Notification) -> Result[None, NotificationError]:
        try:
            asyncio.run(asyncio.wait_for(self._send(notification), timeout=self._timeout))
        except Exception as error:  # a missing daemon, a lost bus or a timeout: all non-fatal
            return Failure(NotificationError(f"Cannot send the notification: {error!r}"))
        return Success(None)

    async def _send(self, notification: Notification) -> None:
        bus = await MessageBus(bus_address=self._bus_address).connect()
        try:
            reply = await bus.call(
                Message(
                    destination=_NAME,
                    path=_PATH,
                    interface=_NAME,
                    member="Notify",
                    signature="susssasa{sv}i",
                    body=[APP_NAME, 0, "", notification.summary, notification.body, [], {}, -1],
                )
            )
            if reply is None or reply.message_type == MessageType.ERROR:
                detail = reply.error_name if reply is not None else "no reply"
                raise RuntimeError(f"Notify failed: {detail}")
        finally:
            bus.disconnect()
