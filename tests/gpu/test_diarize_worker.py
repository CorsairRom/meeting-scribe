"""Real ``diarize`` worker on the GPU with SYNTHETIC audio only (spec 10, assumptions A-4, A-5).

Opt-in: ``uv run pytest -m gpu -s tests/gpu/test_diarize_worker.py``. It loads the gated pyannote
pipeline with the AMBIENT Hugging Face login (``hf auth login`` once, community-1 terms accepted):
run it only with the user's consent. Never records, never plays audio, never reads a meeting. The
token is never read or printed by this test. Prints VRAM before, during and after the worker.
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
from meeting_scribe.speakers.domain.params import DiarizationParams
from tests.unit.speakers.fakes import SPEAKERS_SPEC

pytestmark = pytest.mark.gpu

PIPELINE = os.environ.get(
    "MEETING_SCRIBE_GPU_TEST_PIPELINE", "pyannote/speaker-diarization-community-1"
)
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
    """Silence on the mic; on the system channel two alternating tones (pitch-shifted 'voices').

    Each tone is followed by a pause, as real speech is: a channel with sound in every frame has
    its noise floor at the sound itself and reads as silent (the in-person rule, OQ-4).
    """
    meeting = root / "2026-10-09_000000"
    seconds = np.arange(20 * RATE) / RATE
    phase = seconds % 8
    first = np.sin(2 * np.pi * 180 * seconds) * (phase < 3)
    second = np.sin(2 * np.pi * 330 * seconds) * ((phase >= 4) & (phase < 7))
    _write_chunk(
        meeting / "audio" / "chunk_00000.wav", np.zeros_like(seconds), 0.3 * (first + second)
    )
    words = [{"start": 1.0, "end": 1.4, "text": "uno", "probability": 1.0}]
    document = {"cache_version": 1, "key": {}, "data": {"detected_language": "es", "words": words}}
    (meeting / "words.json").write_text(json.dumps(document), encoding="utf-8")  # as transcribed
    return meeting


def _job(meeting: Path, device: str = "cuda") -> WorkerJob:
    speakers = {
        **DiarizationParams(pipeline=PIPELINE, me_noise_margin_db=10.0).model_dump(),
        **SPEAKERS_SPEC,
    }
    return WorkerJob.build("diarize", str(meeting), device, ProcessingParams(speakers=speakers))


def _worker_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_worker_diarizes_on_the_gpu_caches_and_frees_all_vram(
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
    peak = baseline
    done = threading.Event()

    def poll() -> None:
        nonlocal peak
        while not done.is_set():
            peak = max(peak, _vram_mib())
            time.sleep(0.2)

    sampler = threading.Thread(target=poll, daemon=True)
    sampler.start()
    started = time.monotonic()
    first = runner.run(_job(meeting), 900, collect, threading.Event())
    first_seconds = time.monotonic() - started
    done.set()
    sampler.join(timeout=5)
    time.sleep(1.0)  # let the driver settle after the process exit
    after = _vram_mib()
    leftover = _worker_alive(pids[0]) if pids else False

    started = time.monotonic()
    second = runner.run(_job(meeting), 60, collect, threading.Event())
    second_seconds = time.monotonic() - started

    log = (meeting / "processing.log").read_text(encoding="utf-8")
    with capsys.disabled():
        print(
            f"\nVRAM MiB baseline={baseline} peak_during={peak} after_exit={after}"
            f"\nworker wall time: first {first_seconds:.1f} s, cache-hit {second_seconds:.1f} s"
        )
        for line in log.splitlines():
            if "loaded" in line or "Diarized" in line:
                print(f"log: {line}")

    assert first.unwrap().artifacts == {
        "turns": "turns.json",
        "utterances": "utterances.json",
        "transcript": "transcript.md",
    }
    assert (meeting / "transcript.md").is_file()
    cache = json.loads((meeting / "turns.json").read_text(encoding="utf-8"))
    assert cache["key"]["params"]["device"] == "cuda"
    assert cache["key"]["params"]["channel"] == "system"
    assert "diarization" in cache["data"] and "exclusive_diarization" in cache["data"]
    assert peak > baseline + 300, "the pipeline never reached the GPU"
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
    assert not (meeting / "turns.json").exists()
