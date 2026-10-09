"""``AudioCapture`` adapter: one ffmpeg process writing 2-channel WAV chunks (ADR-002)."""

import contextlib
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import IO

from returns.result import Failure, Result, Success

from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.errors import AudioCaptureError
from meeting_scribe.recording.infrastructure.ffmpeg_command import (
    build_ffmpeg_command,
    pulse_input_args,
)

logger = logging.getLogger(__name__)

type InputsFactory = Callable[[AudioDevices], tuple[list[str], list[str]]]

LOG_FILE_NAME = "ffmpeg.log"
_LOG_TAIL_BYTES = 600


def pulse_inputs(devices: AudioDevices) -> tuple[list[str], list[str]]:
    """Production inputs: ``(microphone, system monitor)``."""
    return pulse_input_args(devices.mic_source), pulse_input_args(devices.monitor_source)


class FfmpegAudioCapture:
    def __init__(
        self,
        ffmpeg_binary: str,
        chunk_seconds: int,
        sample_rate: int,
        stop_timeout_seconds: float,
        inputs_factory: InputsFactory = pulse_inputs,
        startup_grace_seconds: float = 0.5,
        terminate_grace_seconds: float = 2.0,
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary
        self._chunk_seconds = chunk_seconds
        self._sample_rate = sample_rate
        self._stop_timeout = stop_timeout_seconds
        self._inputs_factory = inputs_factory
        self._startup_grace = startup_grace_seconds
        self._terminate_grace = terminate_grace_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self._log_path: Path | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, devices: AudioDevices, audio_dir: Path) -> Result[None, AudioCaptureError]:
        if self.is_running():
            return Failure(AudioCaptureError("Capture is already running."))
        self._release()  # reap a previous, exited process
        mic_args, system_args = self._inputs_factory(devices)
        command = build_ffmpeg_command(
            self._ffmpeg_binary,
            mic_args,
            system_args,
            audio_dir,
            self._chunk_seconds,
            self._sample_rate,
        )
        self._log_path = audio_dir.parent / LOG_FILE_NAME
        try:
            self._log = self._log_path.open("ab")
            self._process = subprocess.Popen(  # noqa: S603 - argv built by build_ffmpeg_command
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._log,
                start_new_session=True,
            )
        except FileNotFoundError:
            self._release()
            return Failure(AudioCaptureError(f"ffmpeg not found: '{self._ffmpeg_binary}'."))
        except OSError as error:
            self._release()
            return Failure(AudioCaptureError(f"Cannot start ffmpeg: {error.strerror or error}"))
        try:
            code = self._process.wait(timeout=self._startup_grace)
        except subprocess.TimeoutExpired:
            logger.info("ffmpeg capture started (pid %s).", self._process.pid)
            return Success(None)
        tail = self._log_tail()
        self._release()
        return Failure(AudioCaptureError(f"ffmpeg exited at startup (code {code}): {tail}"))

    def stop(self) -> Result[None, AudioCaptureError]:
        process = self._process
        if process is None:
            return Success(None)
        try:
            return self._stop(process)
        finally:
            self._release()

    def _stop(self, process: subprocess.Popen[bytes]) -> Result[None, AudioCaptureError]:
        if process.poll() is None:
            self._send_quit(process)
            try:
                process.wait(timeout=self._stop_timeout)
            except subprocess.TimeoutExpired:
                self._force_exit(process)
                logger.error("ffmpeg did not stop on 'q' and was killed.")
                return Failure(AudioCaptureError("ffmpeg did not stop in time and was killed."))
        code = process.returncode
        if code != 0:
            logger.error("ffmpeg exited with code %s.", code)
            return Failure(AudioCaptureError(f"ffmpeg exited with code {code}: {self._log_tail()}"))
        logger.info("ffmpeg capture stopped cleanly.")
        return Success(None)

    def _force_exit(self, process: subprocess.Popen[bytes]) -> None:
        process.terminate()
        try:
            process.wait(timeout=self._terminate_grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    @staticmethod
    def _send_quit(process: subprocess.Popen[bytes]) -> None:
        if process.stdin is None:
            return
        with contextlib.suppress(OSError):  # BrokenPipeError: ffmpeg already closed stdin
            process.stdin.write(b"q")
            process.stdin.flush()

    def _release(self) -> None:
        process, log = self._process, self._log
        self._process = None
        self._log = None
        if process is not None and process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        if log is not None:
            log.close()

    def _log_tail(self) -> str:
        if self._log_path is None:
            return ""
        try:
            data = self._log_path.read_bytes()[-_LOG_TAIL_BYTES:]
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace").strip()
