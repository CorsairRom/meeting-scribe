"""Restart ``meeting-scribe.service`` through the user systemd manager (D-Bus, no ``os.execv``).

Verified against ``man org.freedesktop.systemd1`` (systemd 259): ``Manager.RestartUnit(name, mode)``
returns the job path and "replace" queues the restart over a pending job; ``Manager.LoadUnit(name)``
loads the unit if needed and fails when it does not exist, which makes it a safe reachability probe.
The service holds no connection for this: each call connects, calls once and disconnects. No test
reaches the real systemd (the e2e tests answer as a fake manager on a private bus).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from dbus_fast import BusType, DBusError, Message, MessageType
from dbus_fast.aio import MessageBus
from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.errors import SettingsRestartUnavailableError

logger = logging.getLogger(__name__)

UNIT = "meeting-scribe.service"
MANAGER_NAME = "org.freedesktop.systemd1"
MANAGER_PATH = "/org/freedesktop/systemd1"
MANAGER_INTERFACE = "org.freedesktop.systemd1.Manager"
CALL_TIMEOUT_SECONDS = 5.0

type Connect = Callable[[], Awaitable[MessageBus]]


async def _connect_session() -> MessageBus:
    return await MessageBus(bus_type=BusType.SESSION).connect()


class SystemdRestarter:
    def __init__(self, connect: Connect | None = None) -> None:
        self._connect = connect or _connect_session

    async def is_reachable(self) -> bool:
        return await self._call("LoadUnit", "s", [UNIT])

    async def restart(self) -> Result[None, SettingsRestartUnavailableError]:
        if await self._call("RestartUnit", "ss", [UNIT, "replace"]):
            return Success(None)
        return Failure(SettingsRestartUnavailableError("systemd did not accept the restart."))

    async def _call(self, member: str, signature: str, body: list[str]) -> bool:
        """True when the manager answered with a method return (any other outcome is False)."""
        try:
            bus = await asyncio.wait_for(self._connect(), CALL_TIMEOUT_SECONDS)
        except (OSError, DBusError, TimeoutError, ValueError):
            logger.warning("The service manager could not be reached for %s.", member)
            return False
        try:
            reply = await asyncio.wait_for(
                bus.call(
                    Message(
                        destination=MANAGER_NAME,
                        path=MANAGER_PATH,
                        interface=MANAGER_INTERFACE,
                        member=member,
                        signature=signature,
                        body=body,
                    )
                ),
                CALL_TIMEOUT_SECONDS,
            )
        except (OSError, DBusError, TimeoutError):
            return False
        finally:
            bus.disconnect()
        return reply is not None and reply.message_type == MessageType.METHOD_RETURN
