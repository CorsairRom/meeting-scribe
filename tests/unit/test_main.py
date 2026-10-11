import asyncio
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from meeting_scribe import main as main_module
from meeting_scribe.main import (
    Application,
    ProcessingAdapters,
    RecordingAdapters,
    SummaryAdapters,
    _real_processing_adapters,
    _real_summary_adapters,
    build_processing_config,
    create_app,
    run,
)
from meeting_scribe.meetings.application.processing_controller import ProcessingController
from meeting_scribe.meetings.infrastructure.filesystem_summary_files import FilesystemSummaryFiles
from meeting_scribe.meetings.infrastructure.processing_settings import ProcessingSettings
from meeting_scribe.meetings.infrastructure.subprocess_worker_runner import SubprocessWorkerRunner
from meeting_scribe.recording.application.recording_controller import RecordingController
from meeting_scribe.settings import Settings, StorageSettings, get_settings
from meeting_scribe.speakers.domain.params import AssemblyParams, DiarizationParams
from meeting_scribe.speakers.infrastructure.speakers_settings import SpeakersSettings
from meeting_scribe.summaries.domain.headings import EnglishHeadingCatalog
from meeting_scribe.summaries.infrastructure.openai_provider import OpenAiCompatibleProvider
from meeting_scribe.summaries.infrastructure.secret_service_store import SecretServiceKeyStore
from meeting_scribe.summaries.infrastructure.summaries_settings import SummariesSettings
from meeting_scribe.transcription.domain.params import TranscriptionParams
from meeting_scribe.transcription.infrastructure.transcription_settings import (
    TranscriptionSettings,
)
from tests.unit.meetings.fakes import (
    FakeAudioInspector,
    FakeKeyStore,
    FakeNotifier,
    InMemoryMeetingRepository,
    ScriptedRunner,
)
from tests.unit.meetings.fakes import (
    FakeClock as FakeMeetingsClock,
)
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)
from tests.unit.summaries.fakes import FakeProvider, FakeReleaser


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in ("LOG_LEVEL", "STORAGE_MEETINGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)  # no .env picked up
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # don't leak settings resolved here into later tests


pytestmark = pytest.mark.usefixtures("restore_root_logger")


def test_create_app_returns_given_settings() -> None:
    settings = Settings(log_level="WARNING")

    app = create_app(settings)

    assert isinstance(app, Application)
    assert app.settings is settings


def test_create_app_configures_logging_from_settings() -> None:
    create_app(Settings(log_level="WARNING"))

    assert logging.getLogger().level == logging.WARNING


def test_create_app_defaults_to_cached_settings() -> None:
    assert create_app().settings is get_settings()


def test_run_serves_the_app_and_returns_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[Application] = []

    async def fake_serve(app: Application) -> int:
        served.append(app)
        return 3

    monkeypatch.setattr(main_module, "serve", fake_serve)

    assert run() == 3
    assert isinstance(served[0], Application)


def test_create_app_wires_the_recording_controller_with_the_injected_adapters(
    tmp_path: Path,
) -> None:
    capture = FakeAudioCapture()
    app = create_app(
        Settings(storage=StorageSettings(meetings_dir=tmp_path)),
        recording_adapters=RecordingAdapters(
            clock=FakeClock(),
            device_resolver=FakeDeviceResolver(),
            capture=capture,
            storage=FakeRecordingStorage(),
        ),
    )

    assert isinstance(app.recording, RecordingController)
    assert app.recording.start().unwrap().directory.parent == tmp_path
    assert capture.started_with is not None


def test_create_app_builds_real_adapters_by_default_without_starting_anything() -> None:
    app = create_app(Settings())

    assert app.recording.status().state == "IDLE"  # constructed, not started or resolved


@pytest.fixture
def main_records(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[logging.LogRecord]]:
    """Capture INFO records of the ``meeting_scribe.main`` logger, restoring it afterwards."""
    main_logger = logging.getLogger("meeting_scribe.main")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    monkeypatch.setattr(main_logger, "level", logging.INFO)
    main_logger.addHandler(handler)
    yield records
    main_logger.removeHandler(handler)


def test_create_app_logs_config_source_without_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("STORAGE_MEETINGS_DIR=/secret-value\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    create_app()

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any(str(env_file) in m for m in messages)
    assert not any("secret-value" in m for m in messages)


def test_create_app_logs_environment_only_when_no_env_file(
    main_records: list[logging.LogRecord],
) -> None:
    create_app()

    assert any("no .env file" in r.getMessage() for r in main_records)


def test_create_app_logs_the_env_file_recorded_when_settings_were_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("LOG_LEVEL=WARNING\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    settings = get_settings()
    assert settings.log_level == "WARNING"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "moved"))  # state changes after loading

    create_app()

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any(str(env_file) in m for m in messages)
    assert not any("no .env file" in m for m in messages)


def test_create_app_logs_injected_settings_without_resolving_env_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_records: list[logging.LogRecord]
) -> None:
    env_file = tmp_path / "xdg" / "meeting-scribe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    create_app(Settings(log_level="WARNING"))

    messages = [r.getMessage() for r in main_records if r.levelno == logging.INFO]
    assert any("settings injected by caller" in m for m in messages)
    assert not any(str(env_file) in m for m in messages)


def _every_field_changed() -> Settings:
    """Each field of the three settings classes that feed a job gets a non-default value."""
    return Settings(
        processing=ProcessingSettings(
            device="cpu",
            worker_timeout_factor=2.5,
            worker_min_timeout_seconds=60,
            worker_kill_grace_seconds=1.5,
            notify=False,
            storage_retry_delays_seconds=(1.0, 4.0),
        ),
        transcription=TranscriptionSettings(
            model="tiny",
            language="AUTO",
            compute_type="int8_float16",
            cpu_compute_type="float32",
            beam_size=2,
            vad_filter=False,
            condition_on_previous_text=True,
        ),
        speakers=SpeakersSettings(
            pipeline="org/other-pipeline",
            num_speakers=3,
            min_speakers=2,
            max_speakers=4,
            utterance_gap_seconds=7.5,
            me_dominance_db=4.0,
            me_noise_margin_db=12.0,
            me_min_turn_seconds=0.8,
            sample_max_clips=2,
            sample_target_seconds=10.0,
            sample_min_clip_seconds=2.0,
            sample_max_clip_seconds=5.0,
        ),
    )


def test_the_processing_config_carries_every_setting_into_the_job() -> None:
    config = build_processing_config(_every_field_changed())

    assert config.device == "cpu"
    assert (config.worker_timeout_factor, config.worker_min_timeout_seconds) == (2.5, 60)
    assert config.storage_retry_delays_seconds == (1.0, 4.0)  # the retry budget, every delay
    assert config.params.transcription == {
        "model": "tiny",
        "language": "auto",
        "compute_type": "int8_float16",
        "cpu_compute_type": "float32",
        "beam_size": 2,
        "vad_filter": False,
        "condition_on_previous_text": True,
    }
    assert config.params.speakers == {
        "pipeline": "org/other-pipeline",
        "num_speakers": 3,
        "min_speakers": 2,
        "max_speakers": 4,
        "utterance_gap_seconds": 7.5,
        "me_dominance_db": 4.0,
        "me_noise_margin_db": 12.0,
        "me_min_turn_seconds": 0.8,
        "sample_max_clips": 2,
        "sample_target_seconds": 10.0,
        "sample_min_clip_seconds": 2.0,
        "sample_max_clip_seconds": 5.0,
    }


def test_no_setting_of_the_three_job_classes_is_left_out_of_the_job_spec() -> None:
    params = build_processing_config(Settings()).params

    assert set(params.transcription) == set(TranscriptionSettings.model_fields)
    assert set(params.speakers) == set(SpeakersSettings.model_fields)


def test_the_job_spec_is_accepted_by_the_worker_side_models() -> None:
    params = build_processing_config(_every_field_changed()).params

    assert TranscriptionParams.model_validate(params.transcription).language == "auto"
    assert DiarizationParams.model_validate(params.speakers).pipeline == "org/other-pipeline"
    assert AssemblyParams.model_validate(params.speakers).sample_max_clips == 2


def test_the_real_runner_gets_its_kill_grace_from_the_processing_settings() -> None:
    adapters = _real_processing_adapters(_every_field_changed())

    assert isinstance(adapters.runner, SubprocessWorkerRunner)
    assert adapters.runner._grace == 1.5


def test_create_app_builds_the_processing_controller_without_starting_anything() -> None:
    app = create_app(Settings())

    assert isinstance(app.processing, ProcessingController)
    assert app.processing.queued_ids() == ()  # nothing resumed or queued before serve()


def test_a_recording_on_the_wired_app_holds_back_processing_and_enqueues_on_stop(
    tmp_path: Path,
) -> None:
    repository = InMemoryMeetingRepository()
    runner = ScriptedRunner()
    app = create_app(
        Settings(storage=StorageSettings(meetings_dir=tmp_path)),
        recording_adapters=RecordingAdapters(
            clock=FakeClock(),
            device_resolver=FakeDeviceResolver(),
            capture=FakeAudioCapture(),
            storage=FakeRecordingStorage(),
        ),
        processing_adapters=ProcessingAdapters(
            repository=repository,
            runner=runner,
            audio=FakeAudioInspector(),
            clock=FakeMeetingsClock(),
            notifier=FakeNotifier(),
        ),
    )

    session = app.recording.start().unwrap()
    assert app.processing.run_next() is False  # recording: the loop may not start a worker
    app.recording.stop().unwrap()

    assert repository.meetings[session.id].processing.state == "QUEUED"
    assert app.processing.run_next() is True  # released by the stop
    assert repository.meetings[session.id].processing.state == "DONE"
    assert runner.stages_for(session.id) == ["transcribe", "diarize"]


def test_a_processing_loop_that_outlives_the_shutdown_bound_is_logged_not_fatal(
    main_records: list[logging.LogRecord],
) -> None:
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, args=(5,), daemon=True)
    stuck.start()
    app = create_app(Settings())

    asyncio.run(main_module._stop_processing(app.processing, stuck, 0.05))
    release.set()
    stuck.join()

    assert any(
        r.levelno == logging.ERROR and "did not stop within" in r.getMessage() for r in main_records
    )


# -- summaries wiring (F5.5) --


def test_the_real_summary_adapters_are_built_from_the_settings_without_any_call() -> None:
    adapters = _real_summary_adapters(Settings())

    assert isinstance(adapters.files, FilesystemSummaryFiles)
    assert isinstance(adapters.keys, SecretServiceKeyStore)
    assert isinstance(adapters.local_provider, OpenAiCompatibleProvider)
    assert isinstance(adapters.cloud_provider, OpenAiCompatibleProvider)


def test_the_summary_controller_gets_its_providers_and_labels_from_the_settings() -> None:
    app = create_app(Settings(summaries=SummariesSettings(local_model="m-local")))

    local, cloud = app.summaries.providers()

    assert (local.label, local.cloud, local.available, local.default) == (
        "Local (m-local)",
        False,
        True,
        True,
    )
    assert cloud.label == "cloud: OpenCode Go, deepseek-v4-pro"  # the service owns the label
    assert (cloud.cloud, cloud.available, cloud.reason) == (True, False, "SUMMARY_CLOUD_DISABLED")


def test_recording_processing_and_summaries_share_one_gpu_lease() -> None:
    app = create_app(Settings())

    assert app.summaries._lease is app.processing._lease
    assert app.summaries._on_gpu_free == app.processing.wake  # a freed GPU wakes the queue


def test_injected_summary_adapters_replace_the_real_ones(tmp_path: Path) -> None:
    provider = FakeProvider()
    keys = FakeKeyStore()
    app = create_app(
        Settings(
            storage=StorageSettings(meetings_dir=tmp_path),
            summaries=SummariesSettings(cloud_enabled=True),
        ),
        summary_adapters=SummaryAdapters(
            files=FilesystemSummaryFiles(),
            keys=keys,
            local_provider=provider,
            cloud_provider=provider,
            local_releaser=FakeReleaser(),
            cloud_releaser=FakeReleaser(),
            headings=EnglishHeadingCatalog(),
        ),
    )

    assert app.summaries.providers()[1].available is True
    assert keys.reads == 0  # building the app never reads the key
