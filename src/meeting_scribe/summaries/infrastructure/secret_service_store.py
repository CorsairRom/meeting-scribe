"""The cloud API key from the GNOME Keyring, over the freedesktop Secret Service (ADR-009).

Called from the summary job thread, which has no event loop: each lookup runs its own short
``asyncio.run`` with a fresh bus connection, so nothing is held between requests. The key is
searched by attributes, read through a "plain" session (it only crosses the user's local session
bus socket) and returned as a ``SecretStr``. A locked keyring is reported, never unlocked: that
would pop a prompt from the service. Nothing here logs or quotes the secret.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryCloudKeyLockedError,
    SummaryCloudKeyMissingError,
    SummaryCloudKeyUnavailableError,
    SummaryError,
)

logger = logging.getLogger(__name__)

ATTRIBUTES = {"application": "meeting-scribe", "key": "opencode-api-key"}
LABEL = "Meeting Scribe OpenCode API key"
_NAME = "org.freedesktop.secrets"
_PATH = "/org/freedesktop/secrets"
_SERVICE = "org.freedesktop.Secret.Service"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_LOCKED_MESSAGE = "The GNOME Keyring is locked. Unlock the login keyring and try again."
_UNAVAILABLE_MESSAGE = (
    "The GNOME Keyring service is not reachable (no Secret Service on the session bus). "
    "Check that gnome-keyring is running."
)


def missing_message() -> str:
    """The next step for a missing key, built from the label and attributes the lookup uses."""
    attributes = " ".join(f"{name} {value}" for name, value in ATTRIBUTES.items())
    return (
        "The cloud API key is not stored in the GNOME Keyring. Store it with "
        f'`secret-tool store --label="{LABEL}" {attributes}` '
        "(see docs/runbooks/store-cloud-api-key.md)."
    )


class _LockedError(Exception):
    """The key exists but the keyring is locked."""


class _MissingError(Exception):
    """The keyring answered but holds no usable key."""


class _UnavailableError(Exception):
    """The Secret Service did not answer properly."""


class SecretServiceKeyStore:
    def __init__(
        self, bus_address: str | None = None, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._bus_address = bus_address
        self._timeout = timeout_seconds

    def get_key(self) -> Result[SecretStr, SummaryError]:
        try:
            secret = self._run()
        except _LockedError:
            return Failure(SummaryCloudKeyLockedError(_LOCKED_MESSAGE))
        except _MissingError:
            return Failure(SummaryCloudKeyMissingError(missing_message()))
        except Exception as error:  # no service, a lost bus, an error reply, a timeout
            logger.warning("Secret Service lookup failed: %s", type(error).__name__)
            return Failure(SummaryCloudKeyUnavailableError(_UNAVAILABLE_MESSAGE))
        if not secret:
            return Failure(SummaryCloudKeyMissingError(missing_message()))
        return Success(SecretStr(secret))

    def _run(self) -> str:
        """Run the lookup on its own loop; in a helper thread if this thread already has one."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(asyncio.wait_for(self._read(), timeout=self._timeout))
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, asyncio.wait_for(self._read(), timeout=self._timeout)
            ).result()

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
            try:
                return bytes(secrets[unlocked[0]][2]).decode("utf-8").strip()
            except UnicodeDecodeError:  # the keyring answered, but with no usable key
                raise _MissingError from None
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
            raise _UnavailableError
        return reply.body
