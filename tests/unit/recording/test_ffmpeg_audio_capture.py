"""Capture adapter tests. Only synthetic ``lavfi`` sources or a stub script; never a device.

Every process is reaped by PID in a finalizer. Real ffmpeg always runs with ``-re`` (real-time
pacing) and a hard ``-t`` cap: lavfi without ``-re`` runs as fast as the CPU allows (GO-016).
"""

import array
import math
import os
import signal
import stat
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.errors import AudioCaptureError
from meeting_scribe.recording.infrastructure.ffmpeg_audio_capture import FfmpegAudioCapture

DEVICES = AudioDevices(monitor_source="unused.monitor", mic_source="unused-mic")
HARD_CAP_SECONDS = "15"


def lavfi_inputs(mic: str, system: str) -> Callable[[AudioDevices], tuple[list[str], list[str]]]:
    def factory(_devices: AudioDevices) -> tuple[list[str], list[str]]:
        def args(source: str) -> list[str]:
            return ["-re", "-f", "lavfi", "-t", HARD_CAP_SECONDS, "-i", source]

        return args(mic), args(system)

    return factory


SINE_MIC = "sine=frequency=440:sample_rate=48000"
SILENT_SYSTEM = "anullsrc=r=48000:cl=stereo"
SINE_SYSTEM = "sine=frequency=880:sample_rate=48000"
type CaptureFactory = Callable[..., FfmpegAudioCapture]


@pytest.fixture
def make_capture() -> Iterator[CaptureFactory]:
    created: list[FfmpegAudioCapture] = []

    def factory(**overrides: object) -> FfmpegAudioCapture:
        options: dict[str, object] = {
            "ffmpeg_binary": "ffmpeg",
            "chunk_seconds": 1,
            "sample_rate": 16000,
            "stop_timeout_seconds": 5.0,
            "inputs_factory": lavfi_inputs(SINE_MIC, SILENT_SYSTEM),
            "startup_grace_seconds": 0.3,
            "terminate_grace_seconds": 0.5,
        }
        options.update(overrides)
        capture = FfmpegAudioCapture(**options)  # type: ignore[arg-type]
        created.append(capture)
        return capture

    yield factory
    for capture in created:
        pid = capture.pid
        if pid is not None and capture.is_running():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + 2
                while capture.is_running() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not capture.is_running():
                    break
        capture.stop()  # reap and close the log handle


def _audio_dir(tmp_path: Path) -> Path:
    audio_dir = tmp_path / "session" / "audio"
    audio_dir.mkdir(parents=True)
    return audio_dir


def _wait_for_chunks(audio_dir: Path, count: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(list(audio_dir.glob("chunk_*.wav"))) >= count:
            return
        time.sleep(0.05)
    pytest.fail(f"fewer than {count} chunks after {timeout}s")


def _channel_rms(path: Path) -> tuple[float, float]:
    with wave.open(str(path)) as wav:
        samples = array.array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))

    def rms(values: array.array[int]) -> float:
        return math.sqrt(sum(v * v for v in values) / len(values))

    return rms(samples[0::2]), rms(samples[1::2])


@pytest.mark.ffmpeg
def test_records_two_channel_wav_chunks_and_stops_cleanly(
    make_capture: CaptureFactory, tmp_path: Path
) -> None:
    audio_dir = _audio_dir(tmp_path)
    capture = make_capture()

    assert isinstance(capture.start(DEVICES, audio_dir), Success)
    assert capture.is_running()
    _wait_for_chunks(audio_dir, 2)
    assert isinstance(capture.stop(), Success)

    assert not capture.is_running()
    chunks = sorted(audio_dir.glob("chunk_*.wav"))
    assert [c.name for c in chunks[:2]] == ["chunk_00000.wav", "chunk_00001.wav"]
    for chunk in chunks:
        with wave.open(str(chunk)) as wav:
            assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (2, 16000, 2)
    assert (tmp_path / "session" / "ffmpeg.log").is_file()


@pytest.mark.ffmpeg
def test_channel_zero_is_mic_and_channel_one_is_system(
    make_capture: CaptureFactory, tmp_path: Path
) -> None:
    audio_dir = _audio_dir(tmp_path)
    capture = make_capture(inputs_factory=lavfi_inputs(SINE_MIC, SILENT_SYSTEM))
    capture.start(DEVICES, audio_dir)
    _wait_for_chunks(audio_dir, 2)
    capture.stop()
    mic_left, system_right = _channel_rms(audio_dir / "chunk_00000.wav")
    assert mic_left > 1000
    assert system_right == 0

    swapped_dir = tmp_path / "swapped" / "audio"
    swapped_dir.mkdir(parents=True)
    swapped = make_capture(inputs_factory=lavfi_inputs(SILENT_SYSTEM, SINE_SYSTEM))
    swapped.start(DEVICES, swapped_dir)
    _wait_for_chunks(swapped_dir, 2)
    swapped.stop()
    mic_left, system_right = _channel_rms(swapped_dir / "chunk_00000.wav")
    assert mic_left == 0
    assert system_right > 1000


@pytest.mark.ffmpeg
def test_killed_process_is_detected_as_not_running(
    make_capture: CaptureFactory, tmp_path: Path
) -> None:
    capture = make_capture()
    capture.start(DEVICES, _audio_dir(tmp_path))
    pid = capture.pid
    assert pid is not None

    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while capture.is_running() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not capture.is_running()
    result = capture.stop()  # reaps the crashed process
    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioCaptureError)


@pytest.mark.ffmpeg
def test_startup_failure_returns_failure_with_log_tail(
    make_capture: CaptureFactory, tmp_path: Path
) -> None:
    def broken(_devices: AudioDevices) -> tuple[list[str], list[str]]:
        return ["-f", "lavfi", "-i", "nosuchfilter=x=1"], ["-f", "lavfi", "-i", "anullsrc"]

    capture = make_capture(inputs_factory=broken)

    result = capture.start(DEVICES, _audio_dir(tmp_path))

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioCaptureError)
    assert "nosuchfilter" in str(result.failure())
    assert not capture.is_running()


def test_missing_binary_is_a_failure(make_capture: CaptureFactory, tmp_path: Path) -> None:
    capture = make_capture(ffmpeg_binary=str(tmp_path / "no-such-ffmpeg"))

    result = capture.start(DEVICES, _audio_dir(tmp_path))

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioCaptureError)
    assert "not found" in str(result.failure())


def test_stop_without_start_is_idempotent(make_capture: CaptureFactory) -> None:
    capture = make_capture()

    assert isinstance(capture.stop(), Success)
    assert isinstance(capture.stop(), Success)
    assert not capture.is_running()


@pytest.mark.ffmpeg
def test_starting_twice_fails_while_running(make_capture: CaptureFactory, tmp_path: Path) -> None:
    capture = make_capture()
    capture.start(DEVICES, _audio_dir(tmp_path))

    second = capture.start(DEVICES, _audio_dir(tmp_path / "other"))

    assert isinstance(second, Failure)
    assert "already running" in str(second.failure())
    assert capture.is_running()


def _stubborn_binary(tmp_path: Path) -> Path:
    """Stand-in for ffmpeg that ignores ``q`` and SIGTERM. Opens no device, makes no sound."""
    script = tmp_path / "stubborn"
    script.write_text("#!/bin/sh\ntrap '' TERM\nexec sleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_stop_escalates_to_kill_and_reports_failure(
    make_capture: CaptureFactory, tmp_path: Path
) -> None:
    capture = make_capture(
        ffmpeg_binary=str(_stubborn_binary(tmp_path)),
        stop_timeout_seconds=0.3,
        terminate_grace_seconds=0.3,
        startup_grace_seconds=0.2,
    )
    assert isinstance(capture.start(DEVICES, _audio_dir(tmp_path)), Success)

    result = capture.stop()

    assert isinstance(result, Failure)
    assert "killed" in str(result.failure())
    assert not capture.is_running()
