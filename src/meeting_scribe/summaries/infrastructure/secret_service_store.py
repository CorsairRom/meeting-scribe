"""The cloud API key from the GNOME Keyring, over the freedesktop Secret Service (ADR-009).

Called from the summary job thread, which has no event loop: each lookup runs its own short
``asyncio.run`` with a fresh bus connection, so nothing is held between requests. The key is
searched by attributes, read through a "plain" session (it only crosses the user's local session
bus socket) and returned as a ``SecretStr``. A locked keyring is reported, never unlocked: that
would pop a prompt from the service. Nothing here logs or quotes the secret.
"""

import asyncio
from typing import Any

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryCloudKeyLockedError,
    SummaryCloudKeyMissingError,
    SummaryError,
)

ATTRIBUTES = {"application": "meeting-scribe", "key": "opencode-api-key"}
LABEL = "Meeting Scribe OpenCode API key"
_NAME = "org.freedesktop.secrets"
_PATH = "/org/freedesktop/secrets"
_SERVICE = "org.freedesktop.Secret.Service"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_MISSING_MESSAGE = (
    "The cloud API key is not available in the GNOME Keyring. Store it with "
    '`secret-tool store --label="Meeting Scribe OpenCode API key" application meeting-scribe '
    "key opencode-api-key` (see docs/runbooks/store-cloud-api-key.md)."
)
_LOCKED_MESSAGE = "The GNOME Keyring is locked. Unlock the login keyring and try again."


class _LockedError(Exception):
    """The key exists but the keyring is locked."""


class _MissingError(Exception):
    """The key or the service is not there."""


class SecretServiceKeyStore:
    def __init__(
        self, bus_address: str | None = None, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._bus_address = bus_address
        self._timeout = timeout_seconds

    def get_key(self) -> Result[SecretStr, SummaryError]:
        try:
            secret = asyncio.run(asyncio.wait_for(self._read(), timeout=self._timeout))
        except _LockedError:
            return Failure(SummaryCloudKeyLockedError(_LOCKED_MESSAGE))
        except Exception:  # no service, a lost bus, an error reply or a timeout: all "missing"
            return Failure(SummaryCloudKeyMissingError(_MISSING_MESSAGE))
        if not secret:
            return Failure(SummaryCloudKeyMissingError(_MISSING_MESSAGE))
        return Success(SecretStr(secret))

    async def _read(self) -> str:
        bus = await MessageBus(bus_address=self._bus_address).connect()
        try:
            _output, session = await self._call(
                bus, "OpenSession", "sv", ["plain", Variant("s", "")]
            )
            unlocked, locked = await self._call(bus, "SearchItems", "a{ss}", [ATTRIBUTES])
            if not unlocked:
                raise _LockedError if locked else _MissingError
            (secrets,) = await self._call(bus, "GetSecrets", "aoo", [unlocked[:1], session])
            return bytes(secrets[unlocked[0]][2]).decode("utf-8").strip()
        finally:
            bus.disconnect()

    @staticmethod
    async def _call(bus: MessageBus, member: str, signature: str, body: list[object]) -> list[Any]:
        reply = await bus.call(
            Message(
                destination=_NAME,
                path=_PATH,
                interface=_SERVICE,
                member=member,
                signature=signature,
                body=body,
            )
        )
        if reply is None or reply.message_type == MessageType.ERROR:
            raise _MissingError
        return reply.body
