"""Composition root: resolves settings, configures logging and wires the application."""

import asyncio
import logging
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from dbus_fast import NameFlag, RequestNameReply
from dbus_fast.aio import MessageBus
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.base.infrastructure.logging import configure_logging
from meeting_scribe.handoff import ProcessingRecordingListener
from meeting_scribe.meetings.application.event_hub import ProcessingEventHub
from meeting_scribe.meetings.application.gpu_lease import InMemoryGpuLease
from meeting_scribe.meetings.application.notifications import NotifyOnFinish
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingConfig,
    ProcessingController,
)
from meeting_scribe.meetings.application.review_controller import ReviewController
from meeting_scribe.meetings.application.summary_controller import (
    ConfiguredProvider,
    SummaryConfig,
    SummaryController,
)
from meeting_scribe.meetings.application.summary_events import SummaryEventHub
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.ports import (
    AudioInspector,
    MeetingRepository,
    Notifier,
    SummaryFiles,
    WorkerRunner,
)
from meeting_scribe.meetings.domain.ports import Clock as MeetingsClock
from meeting_scribe.meetings.infrastructure.dbus.meetings_interface import MeetingsInterface
from meeting_scribe.meetings.infrastructure.dbus.summaries_interface import SummariesInterface
from meeting_scribe.meetings.infrastructure.filesystem_meeting_repository import (
    FilesystemMeetingRepository,
)
from meeting_scribe.meetings.infrastructure.filesystem_sample_reader import (
    FilesystemSampleReader,
)
from meeting_scribe.meetings.infrastructure.filesystem_summary_files import FilesystemSummaryFiles
from meeting_scribe.meetings.infrastructure.freedesktop_notifier import FreedesktopNotifier
from meeting_scribe.meetings.infrastructure.subprocess_worker_runner import SubprocessWorkerRunner
from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import WavChunkInspector
from meeting_scribe.preferences.application.model_check import ModelCheck
from meeting_scribe.preferences.application.restart_service import RestartService
from meeting_scribe.preferences.application.settings_service import (
    PreferencesService,
    PreferencesState,
)
from meeting_scribe.preferences.domain.models import MODELS_OK, ModelListing, ModelRow
from meeting_scribe.preferences.domain.ports import (
    EffectiveSettings,
    ServiceRestarter,
    SettingsFileStore,
)
from meeting_scribe.preferences.infrastructure.dbus.settings_interface import SettingsInterface
from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore
from meeting_scribe.preferences.infrastructure.meetings_folder import (
    MeetingsFolderPreparer,
    check_meetings_folder,
)
from meeting_scribe.preferences.infrastructure.systemd_restarter import SystemdRestarter
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
from meeting_scribe.settings import (
    Settings,
    get_settings,
    load_saved_settings,
    xdg_settings_file,
)
from meeting_scribe.speakers.application.render_transcript import RenderTranscript
from meeting_scribe.speakers.application.review_speakers import ReviewSpeakers
from meeting_scribe.speakers.infrastructure.json_utterances_store import JsonUtterancesStore
from meeting_scribe.summaries.application.generate_summary import GenerateSummary
from meeting_scribe.summaries.application.list_models import CLOUD, LOCAL, ListModels
from meeting_scribe.summaries.domain.errors import SummaryModelsUnavailableError
from meeting_scribe.summaries.domain.headings import HeadingCatalog
from meeting_scribe.summaries.domain.ports import (
    CloudKeyStore,
    ModelReleaser,
    NullModelReleaser,
    SummaryProvider,
)
from meeting_scribe.summaries.infrastructure.client_identity import user_agent
from meeting_scribe.summaries.infrastructure.gettext_headings import GettextHeadingCatalog
from meeting_scribe.summaries.infrastructure.model_listing import (
    OllamaModelLister,
    OpenAiCompatibleModelLister,
)
from meeting_scribe.summaries.infrastructure.ollama_provider import OllamaChatProvider
from meeting_scribe.summaries.infrastructure.ollama_releaser import OllamaModelReleaser
from meeting_scribe.summaries.infrastructure.openai_provider import OpenAiCompatibleProvider
from meeting_scribe.summaries.infrastructure.secret_service_store import SecretServiceKeyStore

logger = logging.getLogger(__name__)

UNAVAILABLE = SummaryModelsUnavailableError.error_code
MODEL_CACHE_SECONDS = 600.0
"""How long the last good model list stays valid for checking a ``Set`` (it never fetches)."""

EXIT_OK = 0
EXIT_NAME_TAKEN = 1
EXIT_STOP_FAILED = 2
EXIT_BUS_DISCONNECTED = 3
EXIT_PROCESSING_STUCK = 4

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
class SummaryAdapters:
    """The ports behind ``SummaryController``; tests inject fakes, never the real ones."""

    files: SummaryFiles
    keys: CloudKeyStore
    local_provider: SummaryProvider
    cloud_provider: SummaryProvider
    local_releaser: ModelReleaser
    cloud_releaser: ModelReleaser
    headings: HeadingCatalog
    models: ListModels | None = None  # the Preferences model lists; None where a test has none


@dataclass(frozen=True)
class PreferencesAdapters:
    """The ports behind the preferences use cases; tests inject a tmp store and a fake systemd."""

    store: SettingsFileStore
    restarter: ServiceRestarter
    saved: Mapping[str, str] = field(
        default_factory=dict
    )  # the values the settings were loaded with
    file_problem: str = ""  # SETTINGS_FILE_INVALID when the file could not be read at start


@dataclass(frozen=True)
class Application:
    """Wired application. Later phases add controllers and adapters here."""

    settings: Settings
    recording: RecordingController
    processing: ProcessingController
    review: ReviewController  # speaker review of finished meetings, under the processing lock
    events: ProcessingEventHub  # D-Bus signals and notifications subscribe here
    summaries: SummaryController  # one summary at a time, sharing the GPU lease
    summary_events: SummaryEventHub  # the Summaries1 signals subscribe here
    summary_config: "LiveSummaryConfig"  # the live summary settings; ``apply`` swaps them
    preferences: PreferencesService  # Settings1: read, validate and save the exposed settings
    restart: RestartService  # Settings1.Restart through systemd, only when idle


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


def _real_summary_adapters(settings: Settings) -> SummaryAdapters:
    """Construct the production adapters. Nothing is sent and no key is read until a request."""
    summaries = settings.summaries
    keys = SecretServiceKeyStore()
    return SummaryAdapters(
        files=FilesystemSummaryFiles(),
        keys=keys,
        local_provider=OllamaChatProvider(
            base_url=summaries.local_base_url,
            max_retries=summaries.max_retries,
            read_timeout_seconds=summaries.timeout_seconds,
        ),
        cloud_provider=OpenAiCompatibleProvider(
            base_url=summaries.cloud_base_url,
            cloud=True,
            max_retries=summaries.max_retries,
            read_timeout_seconds=summaries.timeout_seconds,
            key_store=keys,
            user_agent=user_agent(),
            session_header=summaries.cloud_session_header,
        ),
        local_releaser=(
            OllamaModelReleaser(summaries.local_base_url, summaries.local_model)
            if summaries.unload_local_model
            else NullModelReleaser()
        ),
        cloud_releaser=NullModelReleaser(),
        headings=GettextHeadingCatalog(),
        models=ListModels(
            OllamaModelLister(summaries.local_base_url),
            OpenAiCompatibleModelLister(summaries.cloud_base_url, user_agent=user_agent()),
            keys,
        ),
    )


def build_summary_config(settings: Settings, ports: SummaryAdapters) -> SummaryConfig:
    """The two providers as the user sees them; the labels come from here, never from a client."""
    summaries = settings.summaries
    return SummaryConfig(
        local=ConfiguredProvider(
            id="local",
            label=summaries.local_label,
            model=summaries.local_model,
            limits=summaries.limits_for(cloud=False),
            generate=GenerateSummary(
                ports.local_provider, ports.local_releaser, catalog=ports.headings
            ),
        ),
        cloud=ConfiguredProvider(
            id="cloud",
            label=summaries.cloud_label,
            model=summaries.cloud_model,
            limits=summaries.limits_for(cloud=True),
            generate=GenerateSummary(
                ports.cloud_provider, ports.cloud_releaser, catalog=ports.headings
            ),
        ),
        default_provider=summaries.provider,
        language=summaries.language,
        total_timeout_seconds=summaries.total_timeout_seconds,
    )


class LiveSummaryConfig:
    """The summary config the controller reads on every request (``SummaryConfigSource``).

    ``apply`` rebuilds the providers, labels and limits from new settings and swaps the reference
    in one assignment: a running job keeps the config it started with, the next request sees the
    new one. Nothing is sent and no key is read while rebuilding (spec preferences.md, A-5).
    """

    def __init__(
        self,
        settings: Settings,
        ports_for: Callable[[Settings], SummaryAdapters],
        initial_ports: SummaryAdapters,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._model_cache: dict[str, tuple[float, ModelListing]] = {}
        self._ports_for = ports_for
        self._ports = initial_ports
        self._config = build_summary_config(settings, initial_ports)

    def __call__(self) -> SummaryConfig:
        return self._config

    def apply(self, settings: Settings) -> None:
        ports = self._ports_for(settings)
        self._config = build_summary_config(settings, ports)
        self._ports = ports

    def list_models(self, scope: str) -> ModelListing:
        """The models of a scope from the adapters in effect (a ``ModelCatalog`` for Settings1)."""
        use_case = self._ports.models
        if use_case is None:
            return ModelListing(UNAVAILABLE)
        listed = use_case.execute(scope)
        if isinstance(listed, Failure):
            return ModelListing(listed.failure().error_code)
        rows = tuple(ModelRow(m.id, m.supported, m.reason) for m in listed.unwrap())
        listing = ModelListing(MODELS_OK, rows)
        self._model_cache[scope] = (self._clock(), listing)  # the last good list, for ``Set``
        return listing

    def cached_models(self, scope: str) -> ModelListing | None:
        """The last good list of a scope if it is fresh; never touches the network."""
        cached = self._model_cache.get(scope)
        if cached is None or self._clock() - cached[0] > MODEL_CACHE_SECONDS:
            return None
        return cached[1]


class _Activity:
    """True while a recording, a processing job or a summary is active (the restart gate)."""

    def __init__(
        self,
        recording: RecordingController,
        processing: ProcessingController,
        summaries: SummaryController,
    ) -> None:
        self._recording = recording
        self._processing = processing
        self._summaries = summaries

    def busy(self) -> bool:
        return (
            self._recording.status().state == "RECORDING"
            or bool(self._processing.status().active_meeting_id)
            or bool(self._summaries.active_meeting_id)
        )


def _real_preferences_adapters() -> PreferencesAdapters:
    return PreferencesAdapters(JsonSettingsStore(xdg_settings_file()), SystemdRestarter())


def build_preferences(
    settings: Settings, ports: PreferencesAdapters, summary_config: "LiveSummaryConfig"
) -> PreferencesService:
    """Validators come from the settings classes; a live change swaps the summary config only."""

    def apply(effective: EffectiveSettings) -> None:
        if isinstance(effective, Settings):
            summary_config.apply(effective)

    folder = MeetingsFolderPreparer()
    model_checks = {
        key: ModelCheck(summary_config, scope, key)
        for key, scope in (
            ("summaries.local_model", LOCAL),
            ("summaries.cloud_model", CLOUD),
        )
    }
    return PreferencesService(
        ports.store,
        PreferencesState(settings, ports.saved, ports.file_problem),
        Settings.with_overrides,
        apply,
        checks={
            "storage.meetings_dir": lambda raw: check_meetings_folder(raw).map(str),
            **model_checks,
        },
        prepare={"storage.meetings_dir": lambda raw: folder(raw).map(str)},
        rollback={"storage.meetings_dir": folder.rollback},
        commit={"storage.meetings_dir": folder.commit},
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
    summary_adapters: SummaryAdapters | None = None,
    preferences_adapters: PreferencesAdapters | None = None,
) -> Application:
    # Hand-built settings (tests) never ran the sources: say so instead of naming an env file.
    hand_built = settings is not None and not settings.loaded
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if hand_built:
        logger.info("config source: settings injected by caller")
    elif settings.env_files:
        logger.info("config source: env file %s plus process environment", settings.env_files[0])
    else:
        logger.info("config source: no .env file found, using process environment and defaults")
    adapters = recording_adapters or _real_recording_adapters(settings)
    processing_ports = processing_adapters or _real_processing_adapters(settings)
    ports_for: Callable[[Settings], SummaryAdapters] = (
        _real_summary_adapters if summary_adapters is None else (lambda _settings: summary_adapters)
    )
    summary_ports = ports_for(settings)
    summary_config = LiveSummaryConfig(settings, ports_for, summary_ports)
    lease = InMemoryGpuLease()  # one GPU rule for recording, processing and local summaries
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
        lease=lease,
    )
    recording = RecordingController(
        clock=adapters.clock,
        device_resolver=adapters.device_resolver,
        capture=adapters.capture,
        storage=adapters.storage,
        meetings_dir=settings.storage.meetings_dir,
        listener=ProcessingRecordingListener(processing, notifier=processing_ports.notifier),
    )
    review = ReviewController(
        repository=processing_ports.repository,
        lock=processing,
        speakers=ReviewSpeakers(),
        transcripts=RenderTranscript(JsonUtterancesStore()),
        samples=FilesystemSampleReader(),
    )
    summary_events = SummaryEventHub()
    summaries = SummaryController(
        repository=processing_ports.repository,
        lock=processing,
        review=review,
        files=summary_ports.files,
        clock=processing_ports.clock,
        lease=lease,
        keys=summary_ports.keys,
        config=summary_config,
        events=summary_events,
        on_gpu_free=processing.wake,
    )
    preference_ports = preferences_adapters or _real_preferences_adapters()
    preferences = build_preferences(settings, preference_ports, summary_config)
    return Application(
        settings=settings,
        recording=recording,
        processing=processing,
        review=review,
        events=events,
        summaries=summaries,
        summary_events=summary_events,
        summary_config=summary_config,
        preferences=preferences,
        restart=RestartService(
            _Activity(recording, processing, summaries), preference_ports.restarter
        ),
    )


def _start_processing(
    processing: ProcessingController, review: ReviewController
) -> threading.Thread:
    """Resume unfinished jobs, repair stale transcripts, then run the loop in its own thread."""
    resumed = processing.resume()
    if isinstance(resumed, Failure):
        logger.error(
            "Could not list meetings to resume processing: [%s]", resumed.failure().error_code
        )
    else:
        logger.info("Processing resumed %d unfinished meeting(s).", resumed.unwrap())
    repaired = review.repair_stale()
    if isinstance(repaired, Failure):
        logger.error(
            "Could not list meetings to repair transcripts: [%s]", repaired.failure().error_code
        )
    elif repaired.unwrap():
        logger.info("Repaired the transcript of %d meeting(s).", repaired.unwrap())
    thread = threading.Thread(target=processing.serve_forever, name="processing", daemon=True)
    thread.start()
    return thread


async def _stop_processing(
    processing: ProcessingController, thread: threading.Thread, wait_seconds: float
) -> bool:
    """Stop the loop and its worker, bounded; True when it ended. The job stays resumable."""
    processing.shutdown()
    await asyncio.to_thread(thread.join, wait_seconds)
    if thread.is_alive():
        logger.error(
            "The processing loop did not stop within %.0f s; a worker process may be left over.",
            wait_seconds,
        )
    return not thread.is_alive()


async def _stop_summaries(summaries: SummaryController, wait_seconds: float) -> bool:
    """Cancel a running summary and wait for its thread, bounded; True when it ended.

    The wait itself is bounded inside ``shutdown`` (no executor thread is left blocked); a job that
    ignores the cancel is a daemon thread, so it cannot hold the process open.
    """
    stopped = await asyncio.to_thread(summaries.shutdown, wait_seconds)
    if not stopped:
        logger.error("The summary job did not stop within %.0f s.", wait_seconds)
    return stopped


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


def _exit_code(
    disconnected: bool, stop_failed: bool, processing_stopped: bool, summaries_stopped: bool = True
) -> int:
    """Precedence 3, 2, 4; every cause is already logged at ERROR where it happened.

    A summary job that outlived the bound counts as 4, like a stuck processing loop.
    """
    if disconnected:
        return EXIT_BUS_DISCONNECTED
    if stop_failed:
        return EXIT_STOP_FAILED
    return EXIT_OK if processing_stopped and summaries_stopped else EXIT_PROCESSING_STUCK


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
    shutdown failed, ``EXIT_BUS_DISCONNECTED`` (3) if the bus connection was lost,
    ``EXIT_PROCESSING_STUCK`` (4) if the processing loop outlived ``shutdown_timeout`` (a worker
    process may be left over) or a summary job did (a daemon thread, no process).
    Precedence: 3, then 2, then 4.
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
        meetings = MeetingsInterface(app.processing, app.review)
        summaries = SummariesInterface(app.summaries)
        settings = SettingsInterface(app.preferences, app.restart, models=app.summary_config)
        for exported in (recording, meetings, summaries, settings):
            bus.export(OBJECT_PATH, exported)
        reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            logger.error("D-Bus name %s is already owned: another instance is running.", BUS_NAME)
            return EXIT_NAME_TAKEN
        app.events.subscribe(meetings)
        app.summary_events.subscribe(summaries)
        app.preferences.subscribe(settings)
        processing_thread: threading.Thread | None = None
        processing_stopped = summaries_stopped = True
        disconnected = False
        try:
            processing_thread = _start_processing(app.processing, app.review)
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
            summaries_stopped = await _stop_summaries(app.summaries, shutdown_timeout)
            if processing_thread is not None:
                processing_stopped = await _stop_processing(
                    app.processing, processing_thread, shutdown_timeout
                )
            else:
                app.processing.shutdown()
            app.events.unsubscribe(meetings)
            app.summary_events.unsubscribe(summaries)
        code = _exit_code(
            disconnected, isinstance(outcome, Failure), processing_stopped, summaries_stopped
        )
        if code == EXIT_OK:
            logger.info("meeting-scribe stopped.")
        return code
    finally:
        bus.disconnect()
        for signum in handled:
            loop.remove_signal_handler(signum)


def run() -> int:
    """Console-script entry point: serve until interrupted; the return value is the exit code."""
    store = JsonSettingsStore(xdg_settings_file())
    loaded = load_saved_settings(store)
    adapters = PreferencesAdapters(store, SystemdRestarter(), loaded.saved, loaded.file_problem)
    return asyncio.run(serve(create_app(loaded.settings, preferences_adapters=adapters)))
