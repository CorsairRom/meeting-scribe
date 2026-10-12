"""The keyring adapter against a fake ``org.freedesktop.secrets`` on a private bus.

The user's session bus and the real keyring are never contacted; the secret is an obvious fake.
"""

import asyncio
import threading
from typing import Annotated

import pytest
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusSignature
from dbus_fast.service import dbus_method
from returns.result import Failure, Success

from meeting_scribe.base.infrastructure import secret_service
from meeting_scribe.summaries.infrastructure.secret_service_store import SecretServiceKeyStore
from tests.e2e.fake_secret_service import (
    FAKE_SECRET,
    ITEM,
    NAME,
    PATH,
    FakeSecretService,
)

pytestmark = pytest.mark.dbus


def lookup(bus_address: str, service: FakeSecretService | None):
    async def main():
        bus = await MessageBus(bus_address=bus_address).connect()
        try:
            if service is not None:
                bus.export(PATH, service)
                await bus.request_name(NAME)
            store = SecretServiceKeyStore(bus_address=bus_address, timeout_seconds=3.0)
            return await asyncio.to_thread(store.get_key)
        finally:
            bus.disconnect()

    return asyncio.run(main())


def test_an_unlocked_key_is_returned_as_a_masked_secret(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=FAKE_SECRET.encode() + b"\n")

    result = lookup(bus_address, service)

    assert isinstance(result, Success)
    secret = result.unwrap()
    assert secret.get_secret_value() == FAKE_SECRET
    assert FAKE_SECRET not in repr(secret)
    assert service.opened == ["plain"]
    assert service.searched == [{"application": "meeting-scribe", "key": "opencode-api-key"}]
    assert service.unlock_calls == 0


def test_a_missing_key_is_key_missing(bus_address: str) -> None:
    result = lookup(bus_address, FakeSecretService(unlocked=[], locked=[], secret=b""))

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_MISSING"
    assert "secret-tool" in str(result.failure())


def test_a_locked_keyring_is_key_locked_and_is_never_prompted(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[], locked=[ITEM], secret=FAKE_SECRET.encode())

    result = lookup(bus_address, service)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_LOCKED"
    assert service.unlock_calls == 0
    assert FAKE_SECRET not in str(result.failure())


def test_an_empty_secret_is_key_missing(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=b" \n")

    result = lookup(bus_address, service)

    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_MISSING"


def test_no_secret_service_is_key_unavailable_not_an_exception(
    bus_address: str, caplog: pytest.LogCaptureFixture
) -> None:
    result = lookup(bus_address, None)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_UNAVAILABLE"
    assert "not reachable" in str(result.failure())
    assert "secret-tool store" not in str(result.failure())
    assert "Secret Service lookup failed" in caplog.text


def test_an_unreachable_bus_is_key_unavailable(tmp_path) -> None:
    store = SecretServiceKeyStore(bus_address=f"unix:path={tmp_path}/none", timeout_seconds=2.0)

    result = store.get_key()

    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_UNAVAILABLE"


def test_a_stalled_secret_service_times_out_as_unavailable(bus_address: str) -> None:
    class Stalled(FakeSecretService):
        @dbus_method()
        async def SearchItems(  # noqa: N802
            self,
            attributes: Annotated[dict[str, str], DBusSignature("a{ss}")],  # noqa: ARG002
        ) -> Annotated[tuple[list[str], list[str]], DBusSignature("aoao")]:
            await asyncio.sleep(10)
            return [], []

    async def main():
        bus = await MessageBus(bus_address=bus_address).connect()
        try:
            bus.export(PATH, Stalled(unlocked=[], locked=[], secret=b""))
            await bus.request_name(NAME)
            store = SecretServiceKeyStore(bus_address=bus_address, timeout_seconds=0.5)
            return await asyncio.to_thread(store.get_key)
        finally:
            bus.disconnect()

    assert asyncio.run(main()).failure().error_code == "SUMMARY_CLOUD_KEY_UNAVAILABLE"


def test_an_undecodable_secret_is_key_missing(bus_address: str) -> None:
    result = lookup(bus_address, FakeSecretService(unlocked=[ITEM], locked=[], secret=b"\xff\xfe"))

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_MISSING"


def test_the_failure_log_carries_no_secret(
    bus_address: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingDisconnect(MessageBus):
        def disconnect(self) -> None:
            super().disconnect()
            raise RuntimeError(FAKE_SECRET)  # a failure after the secret was read

    monkeypatch.setattr(secret_service, "MessageBus", FailingDisconnect)
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=FAKE_SECRET.encode())

    result = lookup(bus_address, service)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SUMMARY_CLOUD_KEY_UNAVAILABLE"
    assert "RuntimeError" in caplog.text
    assert FAKE_SECRET not in caplog.text
    assert FAKE_SECRET.encode() not in caplog.text.encode()
    assert str(FAKE_SECRET.encode()) not in caplog.text


def test_the_lookup_works_from_a_thread_with_a_running_event_loop(bus_address: str) -> None:
    service = FakeSecretService(unlocked=[ITEM], locked=[], secret=FAKE_SECRET.encode())
    ready, stop = threading.Event(), threading.Event()

    def serve() -> None:
        async def run() -> None:
            bus = await MessageBus(bus_address=bus_address).connect()
            bus.export(PATH, service)
            await bus.request_name(NAME)
            ready.set()
            await asyncio.to_thread(stop.wait)
            bus.disconnect()

        asyncio.run(run())

    server = threading.Thread(target=serve)
    server.start()
    assert ready.wait(5)

    async def caller():
        store = SecretServiceKeyStore(bus_address=bus_address, timeout_seconds=3.0)
        return store.get_key()  # called directly from a thread whose loop is running

    try:
        result = asyncio.run(caller())
    finally:
        stop.set()
        server.join()

    assert isinstance(result, Success)
    assert result.unwrap().get_secret_value() == FAKE_SECRET
