"""Read one secret from the GNOME Keyring over the freedesktop Secret Service (ADR-009, ADR-011).

Shared by the cloud API key (``summaries``) and the Hugging Face token (``model_setup``), which may
not import each other. Each lookup runs its own short ``asyncio.run`` with a fresh bus connection,
so nothing is held between requests and it is safe from a job thread that has no event loop. The
item is searched by attributes and read through a "plain" session (it only crosses the user's local
session bus socket). A locked keyring is reported, never unlocked: that would pop a prompt from the
service. Nothing here logs or quotes the secret.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)

_NAME = "org.freedesktop.secrets"
_PATH = "/org/freedesktop/secrets"
_SERVICE = "org.freedesktop.Secret.Service"
DEFAULT_TIMEOUT_SECONDS = 5.0


class SecretLockedError(Exception):
    """The item exists but the keyring is locked."""


class SecretMissingError(Exception):
    """The keyring answered but holds no usable secret for these attributes."""


class SecretUnavailableError(Exception):
    """The Secret Service is not reachable or did not answer properly."""


class SecretServiceReader:
    def __init__(
        self,
        attributes: Mapping[str, str],
        bus_address: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._attributes = dict(attributes)
        self._bus_address = bus_address
        self._timeout = timeout_seconds

    def read(self) -> str:
        """The secret text, trimmed. Raises ``SecretLockedError``, ``SecretMissingError`` or
        ``SecretUnavailableError``; never prompts."""
        secret = self._guarded(self._read)
        if not secret:
            raise SecretMissingError
        return secret

    def exists(self) -> bool:
        """Whether an item with these attributes exists (locked or not). The secret is not read.

        Raises ``SecretUnavailableError`` when the Secret Service cannot be reached.
        """
        return self._guarded(self._exists)

    def _guarded[T](self, lookup: Callable[[], Coroutine[Any, Any, T]]) -> T:
        try:
            return self._run(lookup)
        except (SecretLockedError, SecretMissingError):
            raise
        except Exception as error:  # no service, a lost bus, an error reply, a timeout
            logger.warning("Secret Service lookup failed: %s", type(error).__name__)
            raise SecretUnavailableError from None

    def _run[T](self, lookup: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Run the lookup on its own loop; in a helper thread if this thread already has one."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(asyncio.wait_for(lookup(), timeout=self._timeout))
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, asyncio.wait_for(lookup(), timeout=self._timeout)
            ).result()

    async def _exists(self) -> bool:
        bus = await MessageBus(bus_address=self._bus_address).connect()
        try:
            unlocked, locked = await self._call(bus, "SearchItems", "a{ss}", [self._attributes])
            return bool(unlocked or locked)
        finally:
            bus.disconnect()

    async def _read(self) -> str:
        bus = await MessageBus(bus_address=self._bus_address).connect()
        try:
            _output, session = await self._call(
                bus, "OpenSession", "sv", ["plain", Variant("s", "")]
            )
            unlocked, locked = await self._call(bus, "SearchItems", "a{ss}", [self._attributes])
            if not unlocked:
                raise SecretLockedError if locked else SecretMissingError
            (secrets,) = await self._call(bus, "GetSecrets", "aoo", [unlocked[:1], session])
            try:
                return bytes(secrets[unlocked[0]][2]).decode("utf-8").strip()
            except UnicodeDecodeError:  # the keyring answered, but with no usable secret
                raise SecretMissingError from None
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
            raise SecretUnavailableError
        return reply.body
