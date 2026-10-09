"""Composition root: resolves settings, configures logging and wires the application."""

import asyncio
import logging
import signal
import threading
from dataclasses import dataclass

from dbus_fast import NameFlag, RequestNameReply
from dbus_fast.aio import MessageBus
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.base.infrastructure.logging import configure_logging
from meeting_scribe.handoff import ProcessingRecordingListener
from meeting_scribe.meetings.application.event_hub import ProcessingEventHub
from meeting_scribe.meetings.application.notifications import NotifyOnFinish
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingConfig,
    ProcessingController,
)
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.ports import (
    AudioInspector,
    MeetingRepository,
    Notifier,
    WorkerRunner,
)
from meeting_scribe.meetings.domain.ports import Clock as MeetingsClock
from meeting_scribe.meetings.infrastructure.dbus.meetings_interface import MeetingsInterface
from meeting_scribe.meetings.infrastructure.filesystem_meeting_repository import (
    FilesystemMeetingRepository,
)
from meeting_scribe.meetings.infrastructure.freedesktop_notifier import FreedesktopNotifier
from meeting_scribe.meetings.infrastructure.subprocess_worker_runner import SubprocessWorkerRunner
from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import WavChunkInspector
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

SHUTDOWN_TIMEOUT_SECONDS = 20.0
"""How long ``serve`` waits for the processing loop to stop its worker (SIGTERM, grace, SIGKILL)."""


@dataclass(frozen=True)
class RecordingAdapters:
    """The ports behind ``RecordingController``; tests inject fakes, never the real ones."""

    clock: Clock
    device_resolver: AudioDeviceResolver
    capture: AudioCapture
    storage: RecordingStorage


@dataclass(frozen=True)
class ProcessingAdapters:
    """The ports behind ``ProcessingController``; tests inject fakes, never the real ones."""

    repository: MeetingRepository
    runner: WorkerRunner
    audio: AudioInspector
    clock: MeetingsClock
    notifier: Notifier


@dataclass(frozen=True)
class Application:
    """Wired application. Later phases add controllers and adapters here."""

    settings: Settings
    recording: RecordingController
    processing: ProcessingController
    events: ProcessingEventHub  # D-Bus signals and notifications subscribe here


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


def _real_processing_adapters(settings: Settings) -> ProcessingAdapters:
    """Construct the production adapters. No process is spawned until a job runs."""
    return ProcessingAdapters(
        repository=FilesystemMeetingRepository(settings.storage.meetings_dir),
        runner=SubprocessWorkerRunner(
            kill_grace_seconds=settings.processing.worker_kill_grace_seconds
        ),
        audio=WavChunkInspector(),
        clock=SystemClock(),
        notifier=FreedesktopNotifier(),
    )


def build_processing_config(settings: Settings) -> ProcessingConfig:
    """Every value a job needs, copied from the settings (the worker reads none, spec 6.2).

    The two params dicts are the settings classes' own fields: the transcription snapshot is the
    cache-key source, and the speakers section holds the diarization knobs and the assembly
    thresholds (each worker-side model picks its fields and ignores the rest).
    """
    processing = settings.processing
    return ProcessingConfig(
        device=processing.device,
        worker_timeout_factor=processing.worker_timeout_factor,
        worker_min_timeout_seconds=processing.worker_min_timeout_seconds,
        params=ProcessingParams(
            transcription=settings.transcription.params().model_dump(),
            speakers=settings.speakers.model_dump(),
        ),
        storage_retry_delays_seconds=processing.storage_retry_delays_seconds,
    )


def create_app(
    settings: Settings | None = None,
    *,
    recording_adapters: RecordingAdapters | None = None,
    processing_adapters: ProcessingAdapters | None = None,
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
    processing_ports = processing_adapters or _real_processing_adapters(settings)
    events = ProcessingEventHub()
    if settings.processing.notify:
        events.subscribe(NotifyOnFinish(processing_ports.notifier))
    processing = ProcessingController(
        repository=processing_ports.repository,
        runner=processing_ports.runner,
        audio=processing_ports.audio,
        clock=processing_ports.clock,
        meetings_dir=settings.storage.meetings_dir,
        config=build_processing_config(settings),
        events=events,
    )
    recording = RecordingController(
        clock=adapters.clock,
        device_resolver=adapters.device_resolver,
        capture=adapters.capture,
        storage=adapters.storage,
        meetings_dir=settings.storage.meetings_dir,
        listener=ProcessingRecordingListener(processing),
    )
    return Application(settings=settings, recording=recording, processing=processing, events=events)


def _start_processing(processing: ProcessingController) -> threading.Thread:
    """Resume unfinished jobs, then run the loop in its own thread (it blocks on workers)."""
    resumed = processing.resume()
    if isinstance(resumed, Failure):
        logger.error(
            "Could not list meetings to resume processing: [%s]", resumed.failure().error_code
        )
    else:
        logger.info("Processing resumed %d unfinished meeting(s).", resumed.unwrap())
    thread = threading.Thread(target=processing.serve_forever, name="processing", daemon=True)
    thread.start()
    return thread


async def _stop_processing(
    processing: ProcessingController, thread: threading.Thread, wait_seconds: float
) -> None:
    """Stop the loop and its worker, bounded; the job stays resumable on disk (AC-16)."""
    processing.shutdown()
    await asyncio.to_thread(thread.join, wait_seconds)
    if thread.is_alive():
        logger.error(
            "The processing loop did not stop within %.0f s; a worker process may be left over.",
            wait_seconds,
        )


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
    shutdown_timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
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
        meetings = MeetingsInterface(app.processing)
        bus.export(OBJECT_PATH, recording)
        bus.export(OBJECT_PATH, meetings)
        reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            logger.error("D-Bus name %s is already owned: another instance is running.", BUS_NAME)
            return EXIT_NAME_TAKEN
        app.events.subscribe(meetings)
        processing_thread: threading.Thread | None = None
        disconnected = False
        try:
            processing_thread = _start_processing(app.processing)
            recording.start_monitor()
            logger.info("meeting-scribe serving %s on the D-Bus session bus.", BUS_NAME)
            if ready is not None:
                ready.set()
            try:
                disconnected = await _wait_for_stop_or_disconnect(stop, bus)
            finally:
                # first: stopping the recording queues its meeting for the next start
                outcome = await recording.shutdown()
        finally:
            if processing_thread is not None:
                await _stop_processing(app.processing, processing_thread, shutdown_timeout)
            else:
                app.processing.shutdown()
            app.events.unsubscribe(meetings)
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
