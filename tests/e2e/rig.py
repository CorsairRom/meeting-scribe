"""Shared e2e rig: the whole service on a private bus, wired with fakes only.

SAFETY: every adapter is a fake. ``Recording1.Start`` records nothing (``FakeAudioCapture``), the
worker runner never spawns a process or loads a model, and notifications go to a fake notifier,
never to the real ``org.freedesktop.Notifications`` of the user's session.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from dbus_fast.aio import MessageBus

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.main import (
    Application,
    ProcessingAdapters,
    RecordingAdapters,
    create_app,
    serve,
)
from meeting_scribe.meetings.domain.meeting import Meeting, RecordingInfo
from meeting_scribe.settings import Settings, StorageSettings
from tests.unit.meetings.fakes import (
    T0,
    FakeAudioInspector,
    InMemoryMeetingRepository,
    ScriptedRunner,
)
from tests.unit.meetings.fakes import FakeClock as FakeMeetingsClock
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)

type Proxy = Any  # dbus-fast builds proxy members at runtime

POLL = 0.05
WAIT = 5.0


class ServiceRig:
    def __init__(self, tmp_path: Path, runner: ScriptedRunner | None = None) -> None:
        self.meetings_dir = tmp_path / "meetings"
        self.capture = FakeAudioCapture()
        self.repository = InMemoryMeetingRepository()
        self.runner = runner or ScriptedRunner()
        self.app: Application = create_app(
            Settings(storage=StorageSettings(meetings_dir=self.meetings_dir)),
            recording_adapters=RecordingAdapters(
                clock=FakeClock(),
                device_resolver=FakeDeviceResolver(),
                capture=self.capture,
                storage=FakeRecordingStorage(),
            ),
            processing_adapters=ProcessingAdapters(
                repository=self.repository,
                runner=self.runner,
                audio=FakeAudioInspector(),
                clock=FakeMeetingsClock(),
            ),
        )

    def seed(self, meeting_id: str, minutes: int = 0) -> Meeting:
        """A meeting already on disk in ``QUEUED`` (as left by a previous run of the service)."""
        recording = RecordingInfo(
            started_at=T0 + timedelta(minutes=minutes),
            stopped_at=T0 + timedelta(minutes=minutes + 5),
        )
        meeting = Meeting.enqueue(meeting_id, self.meetings_dir / meeting_id, recording, T0)
        self.repository.save(meeting).unwrap()
        return meeting

    def state(self, meeting_id: str) -> str:
        return self.repository.meetings[meeting_id].processing.state


@contextlib.asynccontextmanager
async def running(
    rig: ServiceRig, address: str, shutdown_timeout: float | None = None
) -> AsyncIterator[None]:
    ready, stop = asyncio.Event(), asyncio.Event()
    options = {} if shutdown_timeout is None else {"shutdown_timeout": shutdown_timeout}
    task = asyncio.create_task(
        serve(rig.app, bus_address=address, ready=ready, stop=stop, poll_interval=POLL, **options)
    )
    await asyncio.wait_for(ready.wait(), timeout=WAIT)
    try:
        yield
    finally:
        stop.set()
        assert await asyncio.wait_for(task, timeout=WAIT) == 0


async def connect(address: str) -> MessageBus:
    return await MessageBus(bus_address=address).connect()


async def interface(bus: MessageBus, name: str) -> Proxy:
    introspection = await bus.introspect(BUS_NAME, OBJECT_PATH)
    return bus.get_proxy_object(BUS_NAME, OBJECT_PATH, introspection).get_interface(name)


async def eventually(condition: Callable[[], bool], what: str = "condition") -> None:
    """Poll without blocking the loop (the service shares it in these tests)."""
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline, f"{what} not reached"
        await asyncio.sleep(0.01)
