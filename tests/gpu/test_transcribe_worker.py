"""Real ``transcribe`` worker on the GPU with SYNTHETIC audio only (spec 10, assumptions A-1..A-4).

Opt-in: ``uv run pytest -m gpu -s``. Downloads the model on first use (public, ungated). Never
records, never plays audio, never reads a meeting. Prints VRAM before, during and after the
worker, which is the evidence for the GPU assumptions (the numbers are not asserted tightly:
a desktop session moves the baseline).
"""

import json
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from returns.result import Failure, Success

from meeting_scribe.meetings.domain.errors import WorkerReportedError
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressOrNotice,
    StartedEvent,
    WorkerJob,
)
from meeting_scribe.meetings.infrastructure.subprocess_worker_runner import (
    SubprocessWorkerRunner,
)
from meeting_scribe.transcription.domain.params import TranscriptionParams

pytestmark = pytest.mark.gpu

MODEL = os.environ.get("MEETING_SCRIBE_GPU_TEST_MODEL", "large-v3-turbo")
RATE = 16_000
VRAM_TOLERANCE_MIB = 200  # the desktop session moves the baseline by itself


def _vram_mib() -> int:
    out = subprocess.run(  # noqa: S603 - fixed argv, read-only query
        [
            shutil.which("nvidia-smi") or "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out.splitlines()[0])


def _write_chunk(path: Path, left: np.ndarray, right: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.column_stack([left, right]) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(pcm.tobytes())


def _synthetic_meeting(root: Path) -> Path:
    """Silence on the mic, a pure tone on the system channel, then 3 s of silence."""
    meeting = root / "2026-10-09_000000"
    seconds = np.arange(8 * RATE) / RATE
    tone = 0.3 * np.sin(2 * np.pi * 440 * seconds)
    silence = np.zeros(8 * RATE)
    _write_chunk(meeting / "audio" / "chunk_00000.wav", silence, tone)
    quiet = np.zeros(3 * RATE)
    _write_chunk(meeting / "audio" / "chunk_00001.wav", quiet, quiet)
    return meeting


def _job(meeting: Path, device: str = "cuda") -> WorkerJob:
    params = TranscriptionParams(
        model=MODEL,
        language="es",
        compute_type="float16",
        cpu_compute_type="int8",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    spec = ProcessingParams(transcription=params.model_dump(), speakers={})
    return WorkerJob.build("transcribe", str(meeting), device, spec)


def _worker_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _VramSampler:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def _poll(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, _vram_mib())
            time.sleep(0.2)

    def __enter__(self) -> "_VramSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def test_the_worker_transcribes_on_the_gpu_caches_and_frees_all_vram(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    meeting = _synthetic_meeting(tmp_path)
    runner = SubprocessWorkerRunner(kill_grace_seconds=5)
    events: list[ProgressOrNotice] = []
    pids: list[int] = []

    def collect(event: ProgressOrNotice) -> Success[None]:
        events.append(event)
        if isinstance(event, StartedEvent):
            pids.append(event.pid)
        return Success(None)

    baseline = _vram_mib()
    started = time.monotonic()
    with _VramSampler() as sampler:
        first = runner.run(_job(meeting), 900, collect, threading.Event())
    first_seconds = time.monotonic() - started
    time.sleep(1.0)  # let the driver settle after the process exit
    after = _vram_mib()
    leftover = _worker_alive(pids[0]) if pids else False

    started = time.monotonic()
    second = runner.run(_job(meeting), 60, collect, threading.Event())
    second_seconds = time.monotonic() - started

    log = (meeting / "processing.log").read_text(encoding="utf-8")
    with capsys.disabled():
        print(
            f"\nVRAM MiB baseline={baseline} peak_during={sampler.peak} after_exit={after}"
            f"\nworker wall time: first {first_seconds:.1f} s, cache-hit {second_seconds:.1f} s"
        )
        for line in log.splitlines():
            if "loaded" in line or "Transcribed" in line or "Preloaded" in line:
                print(f"log: {line}")

    result = first.unwrap()
    assert result.artifacts == {"words": "words.json"}
    assert result.detected_language == "es"
    assert result.audio is not None and result.audio.total_frames == 11 * RATE
    started_event = next(e for e in events if isinstance(e, StartedEvent))
    assert started_event.device == "cuda/float16"
    cache = json.loads((meeting / "words.json").read_text(encoding="utf-8"))
    assert cache["cache_version"] == 1 and cache["key"]["params"]["device"] == "cuda"
    assert sampler.peak > baseline + 300, "the model never reached the GPU"
    assert after <= baseline + VRAM_TOLERANCE_MIB, "VRAM was not released by the process exit"
    assert not leftover
    assert isinstance(second, Success)
    assert any(isinstance(e, CacheHitEvent) for e in events)


def test_cuda_without_a_visible_device_fails_and_never_runs_on_the_cpu(tmp_path: Path) -> None:
    meeting = _synthetic_meeting(tmp_path)
    runner = SubprocessWorkerRunner(kill_grace_seconds=5, extra_env={"CUDA_VISIBLE_DEVICES": ""})

    result = runner.run(_job(meeting), 120, lambda _: Success(None), threading.Event())

    assert isinstance(result, Failure)
    error = result.failure()
    assert isinstance(error, WorkerReportedError)
    assert error.error_code == "GPU_UNAVAILABLE"
    assert "PROCESSING_DEVICE=cpu" in str(error)
    assert not (meeting / "words.json").exists()
