"""A fake ``org.freedesktop.systemd1`` on the private test bus: it records, it restarts nothing.

SAFETY: the real user systemd is never reached by a test; the service under test is pointed at the
private bus, where this object is the only thing answering as the manager.
"""

from typing import Annotated

from dbus_fast import NameFlag
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method

NAME = "org.freedesktop.systemd1"
PATH = "/org/freedesktop/systemd1"
DBusPath = Annotated[str, DBusSignature("o")]


class FakeManager(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.freedesktop.systemd1.Manager")
        self.loaded: list[str] = []
        self.restarts: list[tuple[str, str]] = []

    @dbus_method()
    def LoadUnit(  # noqa: N802
        self, name: DBusStr
    ) -> DBusPath:
        self.loaded.append(name)
        return "/org/freedesktop/systemd1/unit/fake"

    @dbus_method()
    def RestartUnit(  # noqa: N802
        self, name: DBusStr, mode: DBusStr
    ) -> DBusPath:
        self.restarts.append((name, mode))
        return "/org/freedesktop/systemd1/job/1"


async def start_fake_systemd(address: str) -> tuple[MessageBus, FakeManager]:
    bus = await MessageBus(bus_address=address).connect()
    manager = FakeManager()
    bus.export(PATH, manager)
    await bus.request_name(NAME, NameFlag.DO_NOT_QUEUE)
    return bus, manager
