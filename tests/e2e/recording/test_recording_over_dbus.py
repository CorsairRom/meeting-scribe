"""Recording1 end to end on a private bus. The controller is wired with fakes only.

SAFETY: ``Start`` on a service wired with real adapters would record the microphone, so every
app here is built through ``fake_app`` and never with the default adapters.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from dbus_fast import DBusError, Message, MessageType
from dbus_fast.aio import MessageBus

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.main import Application, RecordingAdapters, create_app, serve
from meeting_scribe.settings import Settings, StorageSettings
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)

# dbus-fast builds proxy members (call_*, get_*, on_*) at runtime, so they are untyped.
type Proxy = Any

pytestmark = pytest.mark.dbus

POLL = 0.05
ERROR = "dev.meetingscribe.Error"


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.capture = FakeAudioCapture()
        self.app: Application = create_app(
            Settings(storage=StorageSettings(meetings_dir=tmp_path / "meetings")),
            recording_adapters=RecordingAdapters(
                clock=FakeClock(),
                device_resolver=FakeDeviceResolver(),
                capture=self.capture,
                storage=FakeRecordingStorage(),
            ),
        )


@contextlib.asynccontextmanager
async def running(app: Application, address: str) -> AsyncIterator[None]:
    ready, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        serve(app, bus_address=address, ready=ready, stop=stop, poll_interval=POLL)
    )
    await asyncio.wait_for(ready.wait(), timeout=5)
    try:
        yield
    finally:
        stop.set()
        assert await asyncio.wait_for(task, timeout=5) == 0


async def connect(address: str) -> MessageBus:
    return await MessageBus(bus_address=address).connect()


async def recording_proxy(bus: MessageBus) -> Proxy:
    introspection = await bus.introspect(BUS_NAME, OBJECT_PATH)
    proxy = bus.get_proxy_object(BUS_NAME, OBJECT_PATH, introspection)
    return proxy.get_interface("dev.meetingscribe.Recording1")


def run_scenario(rig: Rig, address: str, scenario: Callable[[Proxy], Awaitable[None]]) -> None:
    async def main() -> None:
        async with running(rig.app, address):
            client = await connect(address)
            try:
                await scenario(await recording_proxy(client))
            finally:
                client.disconnect()

    asyncio.run(main())


def test_start_then_properties_report_recording(tmp_path: Path, bus_address: str) -> None:
    rig = Rig(tmp_path)

    async def scenario(recording: Proxy) -> None:
        session_id = await recording.call_start()
        assert session_id != ""
        assert await recording.get_state() == "RECORDING"
        assert await recording.get_session_id() == session_id
        assert str(tmp_path / "meetings") in await recording.get_directory()
        assert await recording.get_failure_reason() == ""
        assert await recording.get_elapsed_seconds() >= 0.0

    run_scenario(rig, bus_address, scenario)

    assert rig.capture.stop_calls == 1  # shutdown stopped the active recording


def test_second_start_fails_with_recording_already_active(tmp_path: Path, bus_address: str) -> None:
    async def scenario(recording: Proxy) -> None:
        await recording.call_start()
        with pytest.raises(DBusError) as excinfo:
            await recording.call_start()
        assert excinfo.value.type == f"{ERROR}.RECORDING_ALREADY_ACTIVE"
        assert "already active" in excinfo.value.text

    run_scenario(Rig(tmp_path), bus_address, scenario)


def test_stop_moves_to_stopped_and_a_second_stop_fails(tmp_path: Path, bus_address: str) -> None:
    async def scenario(recording: Proxy) -> None:
        started = await recording.call_start()
        assert await recording.call_stop() == started
        assert await recording.get_state() == "STOPPED"
        with pytest.raises(DBusError) as excinfo:
            await recording.call_stop()
        assert excinfo.value.type == f"{ERROR}.NO_ACTIVE_RECORDING"

    run_scenario(Rig(tmp_path), bus_address, scenario)


def test_capture_crash_is_announced_with_state_changed(tmp_path: Path, bus_address: str) -> None:
    rig = Rig(tmp_path)

    async def scenario(recording: Proxy) -> None:
        changes: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        recording.on_state_changed(
            lambda state, session_id: changes.put_nowait((state, session_id))
        )
        session_id = await recording.call_start()
        assert await asyncio.wait_for(changes.get(), timeout=2) == ("RECORDING", session_id)

        rig.capture.crash("ffmpeg exited with code 1")

        assert await asyncio.wait_for(changes.get(), timeout=2) == ("FAILED", session_id)
        assert await recording.get_state() == "FAILED"
        assert "ffmpeg exited with code 1" in await recording.get_failure_reason()

    run_scenario(rig, bus_address, scenario)


def test_state_changes_also_emit_properties_changed(tmp_path: Path, bus_address: str) -> None:
    async def main() -> None:
        async with running(Rig(tmp_path).app, bus_address):
            client = await connect(bus_address)
            try:
                introspection = await client.introspect(BUS_NAME, OBJECT_PATH)
                proxy = client.get_proxy_object(BUS_NAME, OBJECT_PATH, introspection)
                recording: Proxy = proxy.get_interface("dev.meetingscribe.Recording1")
                properties: Proxy = proxy.get_interface("org.freedesktop.DBus.Properties")
                seen: asyncio.Queue[dict[str, str]] = asyncio.Queue()
                properties.on_properties_changed(
                    lambda iface, changed, _: (
                        seen.put_nowait({key: variant.value for key, variant in changed.items()})
                        if iface == "dev.meetingscribe.Recording1"
                        else None
                    )
                )
                session_id = await recording.call_start()
                changed = await asyncio.wait_for(seen.get(), timeout=2)
                assert changed["State"] == "RECORDING"
                assert changed["SessionId"] == session_id
            finally:
                client.disconnect()

    asyncio.run(main())


def test_second_service_on_the_same_bus_exits_with_an_error(
    tmp_path: Path, bus_address: str, caplog: pytest.LogCaptureFixture
) -> None:
    async def main() -> int:
        async with running(Rig(tmp_path).app, bus_address):
            return await asyncio.wait_for(
                serve(Rig(tmp_path).app, bus_address=bus_address, poll_interval=POLL), timeout=5
            )

    # configure_logging(force=True) strips root handlers (caplog's too): attach to the module.
    main_logger = logging.getLogger("meeting_scribe.main")
    main_logger.addHandler(caplog.handler)
    try:
        exit_code = asyncio.run(main())
    finally:
        main_logger.removeHandler(caplog.handler)

    assert exit_code != 0
    assert any(
        record.levelname == "ERROR" and "another instance is running" in record.getMessage()
        for record in caplog.records
    )


def test_service_methods_are_registered_under_the_published_names(
    tmp_path: Path, bus_address: str
) -> None:
    async def main() -> str:
        async with running(Rig(tmp_path).app, bus_address):
            client = await connect(bus_address)
            try:
                reply = await client.call(
                    Message(
                        destination=BUS_NAME,
                        path=OBJECT_PATH,
                        interface="org.freedesktop.DBus.Introspectable",
                        member="Introspect",
                    )
                )
                assert reply is not None
                assert reply.message_type == MessageType.METHOD_RETURN
                return str(reply.body[0])
            finally:
                client.disconnect()

    xml = asyncio.run(main())

    assert 'interface name="dev.meetingscribe.Recording1"' in xml
    assert 'signal name="StateChanged"' in xml
