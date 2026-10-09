"""Resolves the default PipeWire/PulseAudio sink monitor and microphone through ``pactl``."""

import logging
import subprocess
from typing import Protocol

from returns.result import Failure, Result, Success

from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.errors import AudioDeviceNotFoundError

logger = logging.getLogger(__name__)

_PACTL_TIMEOUT_SECONDS = 5
_MONITOR_SUFFIX = ".monitor"


class CommandRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessCommandRunner:
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv built by this module
            command,
            capture_output=True,
            text=True,
            timeout=_PACTL_TIMEOUT_SECONDS,
            check=False,
        )


class PactlDeviceResolver:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or SubprocessCommandRunner()

    def resolve(self) -> Result[AudioDevices, AudioDeviceNotFoundError]:
        sink = self._query("get-default-sink")
        if isinstance(sink, Failure):
            return sink
        source = self._query("get-default-source")
        if isinstance(source, Failure):
            return source
        mic = source.unwrap()
        if mic.endswith(_MONITOR_SUFFIX):
            return Failure(
                AudioDeviceNotFoundError(
                    "No microphone found: the default input is a monitor of an output."
                )
            )
        devices = AudioDevices(monitor_source=sink.unwrap() + _MONITOR_SUFFIX, mic_source=mic)
        logger.info("Resolved audio devices: mic=%s monitor=%s", mic, devices.monitor_source)
        return Success(devices)

    def _query(self, subcommand: str) -> Result[str, AudioDeviceNotFoundError]:
        try:
            completed = self._runner.run(["pactl", subcommand])
        except FileNotFoundError:
            return Failure(AudioDeviceNotFoundError("'pactl' is not installed or not on PATH."))
        except subprocess.TimeoutExpired:
            return Failure(AudioDeviceNotFoundError(f"'pactl {subcommand}' timed out."))
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            return Failure(
                AudioDeviceNotFoundError(
                    f"'pactl {subcommand}' failed (exit {completed.returncode})."
                )
            )
        if not output:
            return Failure(AudioDeviceNotFoundError(f"'pactl {subcommand}' returned no device."))
        return Success(output)
