"""Ports implemented by recording infrastructure adapters."""

from datetime import datetime
from pathlib import Path
from typing import Protocol

from returns.result import Result

from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.errors import (
    AudioCaptureError,
    AudioDeviceNotFoundError,
    RecordingStorageError,
)
from meeting_scribe.recording.domain.recording_session import RecordingSession


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware current time."""
        ...


class AudioDeviceResolver(Protocol):
    def resolve(self) -> Result[AudioDevices, AudioDeviceNotFoundError]: ...


class AudioCapture(Protocol):
    def start(self, devices: AudioDevices, audio_dir: Path) -> Result[None, AudioCaptureError]: ...

    def stop(self) -> Result[None, AudioCaptureError]: ...

    def is_running(self) -> bool: ...

    def exit_detail(self) -> str:
        """Empty while running or never started, else the exit code and log tail."""
        ...


class RecordingStorage(Protocol):
    def prepare(self, session: RecordingSession) -> Result[RecordingSession, RecordingStorageError]:
        """Create the session folder with its ``audio/`` subfolder."""
        ...
