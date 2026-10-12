"""Shared e2e rig: the whole service on a private bus, wired with fakes only.

SAFETY: every adapter is a fake. ``Recording1.Start`` records nothing (``FakeAudioCapture``), the
worker runner never spawns a process or loads a model, and notifications go to a fake notifier,
never to the real ``org.freedesktop.Notifications`` of the user's session.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from dbus_fast.aio import MessageBus

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe.main import (
    Application,
    ModelSetupAdapters,
    PreferencesAdapters,
    ProcessingAdapters,
    RecordingAdapters,
    SummaryAdapters,
    create_app,
    serve,
)
from meeting_scribe.meetings.domain.meeting import (
    AudioInfo,
    Meeting,
    ProcessingFailure,
    ProcessingParams,
    RecordingInfo,
    SpeakerEntry,
    SpeakerReview,
)
from meeting_scribe.meetings.infrastructure.filesystem_summary_files import FilesystemSummaryFiles
from meeting_scribe.meetings.infrastructure.processing_settings import ProcessingSettings
from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore
from meeting_scribe.preferences.infrastructure.systemd_restarter import SystemdRestarter
from meeting_scribe.settings import Settings
from meeting_scribe.summaries.application.list_models import ListModels
from meeting_scribe.summaries.domain.headings import EnglishHeadingCatalog
from tests.unit.meetings.fakes import (
    T0,
    FakeAudioInspector,
    FakeKeyStore,
    FakeNotifier,
    InMemoryMeetingRepository,
    ScriptedRunner,
)
from tests.unit.meetings.fakes import FakeClock as FakeMeetingsClock
from tests.unit.model_setup.fakes import FakeCacheProbe, FakeRunner, FakeTokenSource
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)
from tests.unit.summaries.fakes import FakeCloudModels, FakeLocalModels, FakeProvider, FakeReleaser

type Proxy = Any  # dbus-fast builds proxy members at runtime

POLL = 0.05
WAIT = 5.0


async def _no_bus() -> MessageBus:
    raise OSError("no bus for the restart in this test")


class ServiceRig:
    def __init__(
        self,
        tmp_path: Path,
        runner: ScriptedRunner | None = None,
        *,
        notify: bool = True,
        bus_address: str | None = None,
        model_setup: ModelSetupAdapters | None = None,
    ) -> None:
        self.meetings_dir = tmp_path / "meetings"
        # The settings come from a tmp ``settings.json`` (never the real config folder); the cloud
        # is selected so both providers are usable. ``bus_address`` is where the restart looks for
        # systemd (a fake on the private bus); without it the restart finds nothing.
        self.settings_file = tmp_path / "config" / "settings.json"
        saved = {"summaries.provider": "cloud", "storage.meetings_dir": str(self.meetings_dir)}
        store = JsonSettingsStore(self.settings_file)
        store.write(saved).unwrap()
        settings = Settings.load(file_values=saved)
        settings.processing = ProcessingSettings(notify=notify)
        self.notifier = FakeNotifier()
        self.capture = FakeAudioCapture()
        self.repository = InMemoryMeetingRepository()
        self.runner = runner or ScriptedRunner()
        self.local_provider = FakeProvider()  # the summaries: no model, no network, no keyring
        self.cloud_provider = FakeProvider()
        self.keys = FakeKeyStore()
        self.local_models = FakeLocalModels()  # the model lists: no Ollama, no network
        self.cloud_models = FakeCloudModels()
        # The models: no keyring, no child process, no download, never the real Hugging Face cache.
        self.model_tokens = FakeTokenSource()
        self.model_probe = FakeCacheProbe()
        self.model_runner = FakeRunner()
        self.app: Application = create_app(
            settings,
            preferences_adapters=PreferencesAdapters(
                store=store,
                restarter=SystemdRestarter(
                    (lambda: MessageBus(bus_address=bus_address).connect())
                    if bus_address
                    else _no_bus
                ),
                saved=saved,
            ),
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
                notifier=self.notifier,
            ),
            model_setup_adapters=model_setup
            or ModelSetupAdapters(self.model_tokens, self.model_probe, self.model_runner),
            summary_adapters=SummaryAdapters(
                files=FilesystemSummaryFiles(),  # real files, under tmp
                keys=self.keys,
                local_provider=self.local_provider,
                cloud_provider=self.cloud_provider,
                local_releaser=FakeReleaser(),
                cloud_releaser=FakeReleaser(),
                headings=EnglishHeadingCatalog(),
                models=ListModels(self.local_models, self.cloud_models, self.keys),
            ),
        )

    def seed(self, meeting_id: str, minutes: int = 0) -> Meeting:
        """A meeting already on disk in ``QUEUED`` (as left by a previous run of the service)."""
        recording = RecordingInfo(
            started_at=T0 + timedelta(minutes=minutes),
            stopped_at=T0 + timedelta(minutes=minutes + 5),
        )
        queued_at = recording.started_at  # resume order follows ``minutes``
        meeting = Meeting.enqueue(meeting_id, self.meetings_dir / meeting_id, recording, queued_at)
        self.repository.save(meeting).unwrap()
        return meeting

    def _recording(self, minutes: int) -> RecordingInfo:
        return RecordingInfo(
            started_at=T0 + timedelta(minutes=minutes),
            stopped_at=T0 + timedelta(minutes=minutes + 5),
        )

    def seed_done(self, meeting_id: str, minutes: int, speakers: int = 2) -> Meeting:
        meeting = Meeting.enqueue(
            meeting_id, self.meetings_dir / meeting_id, self._recording(minutes), T0
        )
        meeting = meeting.with_audio(
            AudioInfo(chunk_count=1, total_frames=16_000 * 90, duration_seconds=90.0)
        )
        meeting = meeting.start(T0, ProcessingParams()).unwrap().transcription_done().unwrap()
        meeting = meeting.diarization_done().unwrap()
        entries = tuple(
            SpeakerEntry(id=f"S{n}", label=f"Speaker {n}") for n in range(1, speakers + 1)
        )
        meeting = meeting.complete(T0, entries, {"transcript": "transcript.md"}, "es").unwrap()
        self.repository.save(meeting).unwrap()
        return meeting

    def seed_reviewable(
        self,
        meeting_id: str,
        minutes: int,
        utterances: list[tuple[str, float, float, str]],
        samples: dict[str, bytes] | None = None,
        review: SpeakerReview | None = None,
    ) -> Meeting:
        """A ``DONE`` meeting whose folder under tmp holds synthetic utterances and samples.

        Speakers ``S1``, ``S2``, ``S3``; only the ids in ``samples`` list a sample file.
        """
        folder = self.meetings_dir / meeting_id
        (folder / "samples").mkdir(parents=True, exist_ok=True)
        rows = [
            {"speaker": who, "start": a, "end": b, "text": text} for who, a, b, text in utterances
        ]
        (folder / "utterances.json").write_text(json.dumps(rows), encoding="utf-8")
        listed = samples or {}
        for speaker_id, content in listed.items():
            (folder / "samples" / f"{speaker_id}.wav").write_bytes(content)
        entries = tuple(
            SpeakerEntry(
                id=f"S{n}",
                label=f"Speaker {n}",
                talk_seconds=float(40 - n * 10),
                utterance_count=n,
                sample=f"samples/S{n}.wav" if f"S{n}" in listed else "",
            )
            for n in (1, 2, 3)
        )
        done = self.seed_done(meeting_id, minutes, speakers=3)
        meeting = done.with_speaker_review(entries, review or SpeakerReview())
        self.repository.save(meeting).unwrap()
        return meeting

    def seed_failed(self, meeting_id: str, minutes: int) -> Meeting:
        meeting = Meeting.enqueue(
            meeting_id, self.meetings_dir / meeting_id, self._recording(minutes), T0
        )
        failure = ProcessingFailure(
            stage="QUEUED", error_code="MEETING_AUDIO_MISSING", message="no chunk"
        )
        meeting = meeting.fail(failure, T0).unwrap()
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
