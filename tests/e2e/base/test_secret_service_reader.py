"""The shared Secret Service reader against a fake ``org.freedesktop.secrets`` on a private bus."""

import asyncio

import pytest
from dbus_fast.aio import MessageBus

from meeting_scribe.base.infrastructure.secret_service import (
    SecretLockedError,
    SecretMissingError,
    SecretServiceReader,
    SecretUnavailableError,
)
from tests.e2e.fake_secret_service import FAKE_SECRET, ITEM, NAME, PATH, FakeSecretService

pytestmark = pytest.mark.dbus

ATTRIBUTES = {"application": "meeting-scribe", "key": "huggingface-token"}


def call(bus_address: str, service: FakeSecretService | None, method: str):
    async def main():
        bus = await MessageBus(bus_address=bus_address).connect()
        try:
            if service is not None:
                bus.export(PATH, service)
                await bus.request_name(NAME)
            reader = SecretServiceReader(ATTRIBUTES, bus_address=bus_address, timeout_seconds=3.0)
            return await asyncio.to_thread(getattr(reader, method))
        finally:
            bus.disconnect()

    return asyncio.run(main())


def test_read_returns_the_trimmed_secret_and_searches_by_the_given_attributes(
    bus_address: str,
) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=FAKE_SECRET.encode() + b"\n")

    assert call(bus_address, service, "read") == FAKE_SECRET
    assert service.searched == [ATTRIBUTES]
    assert service.opened == ["plain"]
    assert service.unlock_calls == 0


def test_read_of_a_missing_secret_raises_missing(bus_address: str) -> None:
    with pytest.raises(SecretMissingError):
        call(bus_address, FakeSecretService(unlocked=[], locked=[], secret=b""), "read")


def test_read_of_a_locked_keyring_raises_locked_and_never_prompts(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[], locked=[ITEM], secret=FAKE_SECRET.encode())

    with pytest.raises(SecretLockedError):
        call(bus_address, service, "read")
    assert service.unlock_calls == 0


def test_read_without_a_secret_service_raises_unavailable(bus_address: str) -> None:
    with pytest.raises(SecretUnavailableError):
        call(bus_address, None, "read")


def test_exists_is_true_for_an_unlocked_or_a_locked_item_without_reading_the_secret(
    bus_address: str,
) -> None:
    unlocked = FakeSecretService(unlocked=[ITEM], locked=[], secret=FAKE_SECRET.encode())
    locked = FakeSecretService(unlocked=[], locked=[ITEM], secret=FAKE_SECRET.encode())

    assert call(bus_address, unlocked, "exists") is True
    assert call(bus_address, locked, "exists") is True


def test_exists_is_false_when_no_item_matches(bus_address: str) -> None:
    assert (
        call(bus_address, FakeSecretService(unlocked=[], locked=[], secret=b""), "exists") is False
    )


def test_exists_without_a_secret_service_raises_unavailable(bus_address: str) -> None:
    with pytest.raises(SecretUnavailableError):
        call(bus_address, None, "exists")
