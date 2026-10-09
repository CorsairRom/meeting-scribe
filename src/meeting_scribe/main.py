"""Composition root: resolves settings, configures logging and wires the application."""

import asyncio
import logging
import signal
from dataclasses import dataclass

from dbus_fast import NameFlag, RequestNameReply
from dbus_fast.aio import MessageBus
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.base.infrastructure.logging import configure_logging
from meeting_scribe.recording.application.recording_controller import RecordingController
from meeting_scribe.recording.domain.ports import (
    AudioCapture,
    AudioDeviceResolver,
    Clock,
    RecordingStorage,
)
from meeting_scribe.recording.infrastructure.dbus.recording_interface import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    RecordingInterface,
)
from meeting_scribe.recording.infrastructure.ffmpeg_audio_capture import FfmpegAudioCapture
from meeting_scribe.recording.infrastructure.filesystem_recording_storage import (
    FilesystemRecordingStorage,
)
from meeting_scribe.recording.infrastructure.pactl_device_resolver import PactlDeviceResolver
from meeting_scribe.recording.infrastructure.system_clock import SystemClock
from meeting_scribe.settings import Settings, get_settings

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_NAME_TAKEN = 1
EXIT_STOP_FAILED = 2
EXIT_BUS_DISCONNECTED = 3


@dataclass(frozen=True)
class RecordingAdapters:
    """The ports behind ``RecordingController``; tests inject fakes, never the real ones."""

    clock: Clock
    device_resolver: AudioDeviceResolver
    capture: AudioCapture
    storage: RecordingStorage


@dataclass(frozen=True)
class Application:
    """Wired application. Later phases add controllers and adapters here."""

    settings: Settings
    recording: RecordingController


def _real_recording_adapters(settings: Settings) -> RecordingAdapters:
    """Construct the production adapters. Nothing starts until ``RecordingController.start``."""
    recording = settings.recording
    return RecordingAdapters(
        clock=SystemClock(),
        device_resolver=PactlDeviceResolver(),
        capture=FfmpegAudioCapture(
            ffmpeg_binary=recording.ffmpeg_binary,
            chunk_seconds=recording.chunk_seconds,
            sample_rate=recording.sample_rate,
            stop_timeout_seconds=recording.stop_timeout_seconds,
        ),
        storage=FilesystemRecordingStorage(),
    )


def create_app(
    settings: Settings | None = None, *, recording_adapters: RecordingAdapters | None = None
) -> Application:
    injected = settings is not None
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if injected:
        logger.info("config source: settings injected by caller")
    elif settings.env_files:
        logger.info("config source: env file %s plus process environment", settings.env_files[0])
    else:
        logger.info("config source: no .env file found, using process environment and defaults")
    adapters = recording_adapters or _real_recording_adapters(settings)
    recording = RecordingController(
        clock=adapters.clock,
        device_resolver=adapters.device_resolver,
        capture=adapters.capture,
        storage=adapters.storage,
        meetings_dir=settings.storage.meetings_dir,
    )
    return Application(settings=settings, recording=recording)


async def _wait_for_stop_or_disconnect(stop: asyncio.Event, bus: MessageBus) -> bool:
    """Wait for ``stop`` or for the bus to close; True when the bus was lost unexpectedly."""
    stop_waiter = asyncio.create_task(stop.wait())
    disconnect_waiter = asyncio.create_task(bus.wait_for_disconnect())
    try:
        await asyncio.wait({stop_waiter, disconnect_waiter}, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set() or not disconnect_waiter.done():
            return False
        cause = None if disconnect_waiter.cancelled() else disconnect_waiter.exception()
        logger.error("The D-Bus connection was lost (%r); stopping the recording.", cause)
        return True
    finally:
        for waiter in (stop_waiter, disconnect_waiter):
            waiter.cancel()
        await asyncio.gather(stop_waiter, disconnect_waiter, return_exceptions=True)


async def serve(
    app: Application,
    bus_address: str | None = None,
    ready: asyncio.Event | None = None,
    stop: asyncio.Event | None = None,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> int:
    """Export the D-Bus API and run until ``stop`` (or SIGINT/SIGTERM when not given).

    Returns the process exit code: ``EXIT_OK`` (0) after a clean shutdown, ``EXIT_NAME_TAKEN`` (1)
    if the bus name is taken, ``EXIT_STOP_FAILED`` (2) if stopping the active recording at
    shutdown failed, ``EXIT_BUS_DISCONNECTED`` (3) if the bus connection was lost.
    """
    loop = asyncio.get_running_loop()
    handled: list[signal.Signals] = []
    if stop is None:
        stop = asyncio.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
            handled.append(signum)
    bus = await MessageBus(bus_address=bus_address).connect()
    try:
        recording = RecordingInterface(app.recording, poll_interval=poll_interval)
        bus.export(OBJECT_PATH, recording)
        reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            logger.error("D-Bus name %s is already owned: another instance is running.", BUS_NAME)
            return EXIT_NAME_TAKEN
        recording.start_monitor()
        logger.info("meeting-scribe serving %s on the D-Bus session bus.", BUS_NAME)
        if ready is not None:
            ready.set()
        disconnected = False
        try:
            disconnected = await _wait_for_stop_or_disconnect(stop, bus)
        finally:
            outcome = await recording.shutdown()
        if disconnected:
            return EXIT_BUS_DISCONNECTED
        if isinstance(outcome, Failure):
            return EXIT_STOP_FAILED  # already logged at ERROR with its code
        logger.info("meeting-scribe stopped.")
        return EXIT_OK
    finally:
        bus.disconnect()
        for signum in handled:
            loop.remove_signal_handler(signum)


def run() -> int:
    """Console-script entry point: serve until interrupted; the return value is the exit code."""
    return asyncio.run(serve(create_app()))
