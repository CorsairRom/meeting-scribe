"""The real notifier adapter against a fake ``org.freedesktop.Notifications`` on a private bus.

The user's session bus and its notification daemon are never contacted.
"""

import asyncio
from typing import Annotated

import pytest
from dbus_fast import Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusSignature, DBusStr, DBusUInt32
from dbus_fast.service import ServiceInterface, dbus_method
from returns.result import Failure, Success

from meeting_scribe.meetings.domain.notification import Notification
from meeting_scribe.meetings.infrastructure.freedesktop_notifier import FreedesktopNotifier

pytestmark = pytest.mark.dbus

NAME = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


class FakeNotificationServer(ServiceInterface):
    def __init__(self) -> None:
        super().__init__(NAME)
        self.calls: list[tuple[str, int, str, str, str, list[str], int]] = []

    @dbus_method()
    def Notify(  # noqa: N802 - the D-Bus member name
        self,
        app_name: DBusStr,
        replaces_id: DBusUInt32,
        app_icon: DBusStr,
        summary: DBusStr,
        body: DBusStr,
        actions: Annotated[list[str], DBusSignature("as")],
        hints: Annotated[dict[str, Variant], DBusSignature("a{sv}")],  # noqa: ARG002
        expire_timeout: Annotated[int, DBusSignature("i")],
    ) -> DBusUInt32:
        self.calls.append((app_name, replaces_id, app_icon, summary, body, actions, expire_timeout))
        return 7


def test_a_notification_reaches_the_notification_service(bus_address: str) -> None:
    server = FakeNotificationServer()

    async def main() -> object:
        bus = await MessageBus(bus_address=bus_address).connect()
        try:
            bus.export(PATH, server)
            await bus.request_name(NAME)
            notifier = FreedesktopNotifier(bus_address=bus_address)
            return await asyncio.to_thread(
                notifier.notify, Notification(summary="Meeting processed: 2 speakers", body="b")
            )
        finally:
            bus.disconnect()

    assert isinstance(asyncio.run(main()), Success)
    assert server.calls == [("Meeting Scribe", 0, "", "Meeting processed: 2 speakers", "b", [], -1)]


def test_a_missing_notification_service_is_a_typed_failure_not_an_exception(
    bus_address: str,
) -> None:
    notifier = FreedesktopNotifier(bus_address=bus_address, timeout_seconds=2.0)

    result = notifier.notify(Notification(summary="x"))

    assert isinstance(result, Failure)
    assert result.failure().error_code == "NOTIFICATION_FAILED"


def test_an_unreachable_bus_is_a_typed_failure(tmp_path) -> None:
    notifier = FreedesktopNotifier(
        bus_address=f"unix:path={tmp_path}/no-such-socket", timeout_seconds=2.0
    )

    result = notifier.notify(Notification(summary="x"))

    assert isinstance(result, Failure)
    assert result.failure().error_code == "NOTIFICATION_FAILED"
