"""In-memory fakes for the recording ports. No audio device is ever touched."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.errors import (
    AudioCaptureError,
    AudioDeviceNotFoundError,
    RecordingStorageError,
)
from meeting_scribe.recording.domain.recording_session import RecordingSession

T0 = datetime(2026, 10, 9, 9, 15, 0, tzinfo=timezone(timedelta(hours=-5)))


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class FakeDeviceResolver:
    def __init__(self, error: AudioDeviceNotFoundError | None = None) -> None:
        self.error = error
        self.calls = 0

    def resolve(self) -> Result[AudioDevices, AudioDeviceNotFoundError]:
        self.calls += 1
        if self.error is not None:
            return Failure(self.error)
        return Success(AudioDevices(monitor_source="fake.monitor", mic_source="fake-mic"))


class FakeAudioCapture:
    def __init__(
        self,
        start_error: AudioCaptureError | None = None,
        stop_error: AudioCaptureError | None = None,
    ) -> None:
        self.start_error = start_error
        self.stop_error = stop_error
        self.running = False
        self.started_with: tuple[AudioDevices, Path] | None = None
        self.stop_calls = 0

    def start(self, devices: AudioDevices, audio_dir: Path) -> Result[None, AudioCaptureError]:
        if self.start_error is not None:
            return Failure(self.start_error)
        self.started_with = (devices, audio_dir)
        self.running = True
        return Success(None)

    def stop(self) -> Result[None, AudioCaptureError]:
        self.stop_calls += 1
        self.running = False
        if self.stop_error is not None:
            return Failure(self.stop_error)
        return Success(None)

    def is_running(self) -> bool:
        return self.running

    def crash(self) -> None:
        self.running = False


class FakeRecordingStorage:
    def __init__(self, error: RecordingStorageError | None = None) -> None:
        self.error = error
        self.prepared: list[RecordingSession] = []

    def prepare(self, session: RecordingSession) -> Result[RecordingSession, RecordingStorageError]:
        if self.error is not None:
            return Failure(self.error)
        self.prepared.append(session)
        return Success(session)
