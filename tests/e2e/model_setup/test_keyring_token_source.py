"""The Hugging Face token read from a fake Secret Service on a private bus (AC-17).

The user's session bus and the real keyring are never contacted; the token is an obvious fake.
"""

import asyncio

import pytest
from dbus_fast.aio import MessageBus
from returns.result import Failure, Success

from meeting_scribe.model_setup.infrastructure.keyring_token_source import (
    ATTRIBUTES,
    LABEL,
    KeyringTokenSource,
)
from tests.e2e.fake_secret_service import ITEM, NAME, PATH, FakeSecretService
from tests.unit.model_setup.fakes import SENTINEL

pytestmark = pytest.mark.dbus


def call(bus_address: str, service: FakeSecretService | None, method: str):
    async def main():
        bus = await MessageBus(bus_address=bus_address).connect()
        try:
            if service is not None:
                bus.export(PATH, service)
                await bus.request_name(NAME)
            source = KeyringTokenSource(bus_address=bus_address, timeout_seconds=3.0)
            return await asyncio.to_thread(getattr(source, method))
        finally:
            bus.disconnect()

    return asyncio.run(main())


def test_the_item_is_the_one_the_review_app_writes() -> None:
    assert ATTRIBUTES == {"application": "meeting-scribe", "key": "huggingface-token"}
    assert LABEL == "Meeting Scribe Hugging Face token"


def test_a_stored_token_is_returned_masked_and_the_item_is_found_by_its_attributes(
    bus_address: str,
) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=SENTINEL.encode() + b"\n")

    result = call(bus_address, service, "read_token")

    assert isinstance(result, Success)
    token = result.unwrap()
    assert token is not None
    assert token.get_secret_value() == SENTINEL
    assert SENTINEL not in repr(token)
    assert service.searched == [ATTRIBUTES]


def test_no_stored_token_is_none_so_the_ambient_login_is_used(bus_address: str) -> None:
    result = call(bus_address, FakeSecretService(unlocked=[], locked=[], secret=b""), "read_token")

    assert result == Success(None)


def test_no_secret_service_is_none_so_the_ambient_login_is_used(bus_address: str) -> None:
    assert call(bus_address, None, "read_token") == Success(None)


def test_a_locked_keyring_is_an_error_and_is_never_prompted(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[], locked=[ITEM], secret=SENTINEL.encode())

    result = call(bus_address, service, "read_token")

    assert isinstance(result, Failure)
    assert result.failure().error_code == "MODELS_TOKEN_KEYRING_LOCKED"
    assert service.unlock_calls == 0
    assert SENTINEL not in str(result.failure())


def test_token_stored_checks_the_item_without_reading_the_secret(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=SENTINEL.encode())

    assert call(bus_address, service, "token_stored") is True
    assert service.opened == []  # no session: the secret was never requested


def test_token_stored_is_true_for_a_locked_item_and_false_when_absent_or_unreachable(
    bus_address: str,
) -> None:
    locked = FakeSecretService(unlocked=[], locked=[ITEM], secret=b"")
    empty = FakeSecretService(unlocked=[], locked=[], secret=b"")

    assert call(bus_address, locked, "token_stored") is True
    assert call(bus_address, empty, "token_stored") is False
    assert call(bus_address, None, "token_stored") is False
