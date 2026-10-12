"""``Models1`` end to end on a private bus (AC-16, AC-17, AC-18).

A fake download runner, a fake key store and a fake cache probe: no network, no model, no keyring,
no child process. A bus monitor records every message on the private bus to prove the token never
crosses D-Bus.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from dbus_fast import DBusError, Message, MessageType
from dbus_fast.aio import MessageBus

from meeting_scribe.main import ModelSetupAdapters
from meeting_scribe.model_setup.domain.errors import ModelsDownloadReportedError
from meeting_scribe.model_setup.domain.models import CacheStatus
from tests.e2e.rig import Proxy, ServiceRig, connect, eventually, interface, running
from tests.unit.model_setup.fakes import (
    SENTINEL,
    FakeCacheProbe,
    FakeRunner,
    FakeTokenSource,
)

pytestmark = pytest.mark.dbus

IFACE = "dev.meetingscribe.Models1"
ERROR = "dev.meetingscribe.Error"
NONE = CacheStatus(False, False)
BOTH = CacheStatus(True, True)


class Session(NamedTuple):
    client: MessageBus
    models: Proxy
    props: Proxy


def _run(rig: ServiceRig, address: str, scenario: Callable[[Session], Awaitable[None]]) -> None:
    async def main() -> None:
        async with running(rig, address):
            client = await connect(address)
            try:
                session = Session(
                    client,
                    await interface(client, IFACE),
                    await interface(client, "org.freedesktop.DBus.Properties"),
                )
                await scenario(session)
            finally:
                client.disconnect()

    asyncio.run(main())


def _rig(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    tokens: FakeTokenSource | None = None,
    probe: FakeCacheProbe | None = None,
) -> ServiceRig:
    adapters = ModelSetupAdapters(
        tokens or FakeTokenSource(),
        probe or FakeCacheProbe(NONE, BOTH),
        runner or FakeRunner(),
    )
    return ServiceRig(tmp_path, model_setup=adapters)


def _plain(raw: dict) -> dict:
    return {key: variant.value for key, variant in raw.items()}


async def _error(call: Awaitable) -> DBusError:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value


class Monitor:
    """Every message that crosses the private bus, as text (a bus monitor sees all of them)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    @classmethod
    async def attach(cls, address: str) -> tuple["Monitor", MessageBus]:
        monitor = cls()
        bus = await MessageBus(bus_address=address).connect()
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus.Monitoring",
                member="BecomeMonitor",
                signature="asu",
                body=[[], 0],
            )
        )
        assert reply is not None
        assert reply.message_type == MessageType.METHOD_RETURN, reply.body
        # Only now: a handler that returns True would swallow the reply to BecomeMonitor itself.
        bus.add_message_handler(monitor._on_message)
        return monitor, bus

    def _on_message(self, message: Message) -> bool:
        """Returning True marks it handled: a monitor must never answer (the daemon closes it)."""
        self.messages.append(
            f"{message.message_type.name} {message.sender} {message.destination} "
            f"{message.path} {message.interface} {message.member} "
            f"{message.error_name} {message.body!r}"
        )
        return True

    def text(self) -> str:
        return "\n".join(self.messages)


def test_get_status_reports_pending_with_every_field(tmp_path: Path, bus_address: str) -> None:
    rig = _rig(tmp_path, probe=FakeCacheProbe(NONE), tokens=FakeTokenSource(SENTINEL))

    async def scenario(session: Session) -> None:
        status = _plain(await session.models.call_get_status())

        assert status == {
            "state": "PENDING",
            "whisper_cached": False,
            "pipeline_cached": False,
            "token_stored": True,
            "step": 0,
            "total_steps": 0,
            "failure_code": "",
        }
        assert await session.models.get_active_download() is False

    _run(rig, bus_address, scenario)


def test_get_status_reports_ready_when_both_are_cached(tmp_path: Path, bus_address: str) -> None:
    rig = _rig(tmp_path, probe=FakeCacheProbe(BOTH))

    async def scenario(session: Session) -> None:
        status = _plain(await session.models.call_get_status())

        assert status["state"] == "READY"
        assert (status["whisper_cached"], status["pipeline_cached"]) == (True, True)

    _run(rig, bus_address, scenario)


def test_download_replies_at_once_then_signals_progress_and_the_end(
    tmp_path: Path, bus_address: str
) -> None:
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    rig = _rig(tmp_path, runner=runner)
    progress: list[tuple[str, int, int]] = []
    finished: list[tuple[str, str]] = []
    active: list[bool] = []

    async def scenario(session: Session) -> None:
        models = session.models
        models.on_models_progress(lambda m, s, t: progress.append((m, s, t)))
        models.on_models_finished(lambda state, code: finished.append((state, code)))
        session.props.on_properties_changed(
            lambda iface, values, _: (
                active.append(values["ActiveDownload"].value)
                if iface == IFACE and "ActiveDownload" in values
                else None
            )
        )

        assert await models.call_download() == "DOWNLOADING"

        await eventually(lambda: len(progress) == 2, "both progress signals")
        assert progress == [("pipeline", 1, 2), ("whisper", 2, 2)]
        assert await models.get_active_download() is True
        status = _plain(await models.call_get_status())
        assert (status["state"], status["step"], status["total_steps"]) == ("DOWNLOADING", 2, 2)
        assert finished == []
        gate.set()
        await eventually(lambda: finished == [("READY", "")], "the finished signal")
        assert await models.get_active_download() is False
        assert _plain(await models.call_get_status())["state"] == "READY"
        await eventually(lambda: active == [True, False], "ActiveDownload changes")

    _run(rig, bus_address, scenario)


def test_a_second_download_while_one_runs_is_downloads_active_and_cancel_stops_it(
    tmp_path: Path, bus_address: str
) -> None:
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    rig = _rig(tmp_path, runner=runner, probe=FakeCacheProbe(NONE))
    finished: list[tuple[str, str]] = []

    async def scenario(session: Session) -> None:
        models = session.models
        models.on_models_finished(lambda state, code: finished.append((state, code)))
        assert await models.call_download() == "DOWNLOADING"

        error = await _error(models.call_download())
        assert error.type == f"{ERROR}.MODELS_DOWNLOAD_ACTIVE"

        assert await models.call_cancel_download() is True
        await eventually(lambda: finished == [("CANCELLED", "")], "the cancelled signal")
        assert await models.call_cancel_download() is False  # nothing runs any more
        assert _plain(await models.call_get_status())["state"] == "PENDING"

    _run(rig, bus_address, scenario)


def test_a_failed_download_ends_with_failed_and_its_code(tmp_path: Path, bus_address: str) -> None:
    runner = FakeRunner(failure=ModelsDownloadReportedError("DIARIZATION_MODEL_ACCESS_DENIED", "x"))
    rig = _rig(tmp_path, runner=runner)
    finished: list[tuple[str, str]] = []

    async def scenario(session: Session) -> None:
        session.models.on_models_finished(lambda state, code: finished.append((state, code)))
        await session.models.call_download()

        await eventually(lambda: len(finished) == 1, "the finished signal")
        assert finished == [("FAILED", "DIARIZATION_MODEL_ACCESS_DENIED")]
        status = _plain(await session.models.call_get_status())
        assert (status["state"], status["failure_code"]) == (
            "FAILED",
            "DIARIZATION_MODEL_ACCESS_DENIED",
        )

    _run(rig, bus_address, scenario)


def test_a_locked_keyring_ends_with_failed_and_the_runner_never_starts(
    tmp_path: Path, bus_address: str
) -> None:
    runner = FakeRunner()
    rig = _rig(tmp_path, runner=runner, tokens=FakeTokenSource(locked=True))
    finished: list[tuple[str, str]] = []

    async def scenario(session: Session) -> None:
        session.models.on_models_finished(lambda state, code: finished.append((state, code)))
        assert await session.models.call_download() == "DOWNLOADING"

        await eventually(lambda: len(finished) == 1, "the finished signal")
        assert finished == [("FAILED", "MODELS_TOKEN_KEYRING_LOCKED")]
        assert runner.runs == []

    _run(rig, bus_address, scenario)


def test_download_with_both_models_cached_replies_ready_and_runs_nothing(
    tmp_path: Path, bus_address: str
) -> None:
    runner = FakeRunner()
    rig = _rig(tmp_path, runner=runner, probe=FakeCacheProbe(BOTH))

    async def scenario(session: Session) -> None:
        assert await session.models.call_download() == "READY"
        assert runner.runs == []

    _run(rig, bus_address, scenario)


def test_token_never_crosses_dbus(tmp_path: Path, bus_address: str) -> None:
    """The runner gets the token (the keyring read worked); no D-Bus message carries it."""
    runner = FakeRunner()
    rig = _rig(
        tmp_path,
        runner=runner,
        tokens=FakeTokenSource(SENTINEL),
        probe=FakeCacheProbe(NONE, NONE, BOTH),  # status, the fresh check, the check after
    )
    finished: list[tuple[str, str]] = []
    seen: list[str] = []

    async def scenario(session: Session) -> None:
        monitor, monitor_bus = await Monitor.attach(bus_address)
        try:
            session.models.on_models_finished(lambda state, code: finished.append((state, code)))
            seen.append(repr(await session.models.call_get_status()))
            seen.append(repr(await session.models.call_download()))
            await eventually(lambda: len(finished) == 1, "the finished signal")
            seen.append(repr(await session.models.call_get_status()))
            await asyncio.sleep(0.1)  # let the monitor drain
        finally:
            monitor_bus.disconnect()
        traffic = monitor.text()
        assert "GetStatus" in traffic
        assert "ModelsFinished" in traffic  # the monitor really saw the service's traffic
        assert SENTINEL not in traffic
        assert SENTINEL.encode().hex() not in traffic
        assert SENTINEL not in " ".join(seen)

    _run(rig, bus_address, scenario)

    assert runner.runs and runner.runs[0][0] == SENTINEL  # it did reach the download process


def test_the_interface_exposes_exactly_the_documented_members_and_no_token(
    tmp_path: Path, bus_address: str
) -> None:
    """Nothing in the interface can carry a token: three methods, two signals, no setter."""
    rig = _rig(tmp_path)

    async def scenario(session: Session) -> None:
        raw = await session.client.introspect(
            "dev.meetingscribe.Service", "/dev/meetingscribe/Service"
        )
        names = {i.name for i in raw.interfaces}
        assert IFACE in names
        members = {m.name for i in raw.interfaces if i.name == IFACE for m in i.methods}
        assert members == {"GetStatus", "Download", "CancelDownload"}
        signals = {s.name for i in raw.interfaces if i.name == IFACE for s in i.signals}
        assert signals == {"ModelsProgress", "ModelsFinished"}

    _run(rig, bus_address, scenario)
