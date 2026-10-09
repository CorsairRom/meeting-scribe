import io
import json
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.base.domain.errors import GpuUnavailableError
from meeting_scribe.worker import build_transcribe_stage, main
from tests.unit.transcription.fakes import PARAMS
from tests.unit.transcription.test_faster_whisper_transcriber import (
    FakeBackend,
    FakeModel,
    S,
    W,
)


def _meeting(tmp_path: Path) -> Path:
    audio = tmp_path / "audio"
    audio.mkdir()
    with wave.open(str(audio / "chunk_00000.wav"), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(np.zeros((32_000, 2), dtype="<i2").tobytes())
    return tmp_path


def _spec(meeting: Path, device: str = "cuda") -> str:
    return json.dumps(
        {
            "protocol": 1,
            "stage": "transcribe",
            "meeting_dir": str(meeting),
            "device": device,
            "transcription": PARAMS.model_dump(),
            "speakers": {},
        }
    )


def _drive(meeting: Path, backend: FakeBackend, device: str = "cuda"):
    events = io.StringIO()
    code = main(
        ["transcribe"],
        stdin=io.StringIO(_spec(meeting, device)),
        events=events,
        stages={"transcribe": build_transcribe_stage(backend)},
    )
    return code, [json.loads(line) for line in events.getvalue().splitlines()]


def test_the_transcribe_stage_runs_end_to_end_with_a_fake_model(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path)
    backend = FakeBackend(FakeModel([S(0.0, 1.0, " hola", [W(" hola", 0.1, 0.9)])]))

    code, events = _drive(meeting, backend)

    assert code == 0
    assert [e["event"] for e in events] == ["started", "progress", "result"]
    assert events[0]["device"] == "cuda/float16"
    assert events[-1]["artifacts"] == {"words": "words.json"}
    cache = json.loads((meeting / "words.json").read_text(encoding="utf-8"))
    assert cache["data"]["words"][0]["text"] == "hola"


def test_a_second_run_is_a_cache_hit_and_loads_no_model(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path)
    _drive(meeting, FakeBackend(FakeModel([])))
    backend = FakeBackend(FakeModel([]))

    code, events = _drive(meeting, backend)

    assert code == 0
    assert [e["event"] for e in events] == ["cache_hit", "result"]
    assert backend.calls == []


def test_a_gpu_error_is_one_error_event_with_the_exit_code_1(tmp_path: Path) -> None:
    code, events = _drive(_meeting(tmp_path), FakeBackend(cuda_devices=0))

    assert code == 1
    assert events[-1]["event"] == "error"
    assert events[-1]["error_code"] == GpuUnavailableError.error_code
    assert "PROCESSING_DEVICE=cpu" in events[-1]["message"]


def test_an_unexpected_exception_is_an_error_event_never_a_traceback_on_the_stream(
    tmp_path: Path,
) -> None:
    def explode(job, emit):  # noqa: ARG001
        raise RuntimeError("boom")

    events = io.StringIO()
    code = main(
        ["transcribe"],
        stdin=io.StringIO(_spec(_meeting(tmp_path))),
        events=events,
        stages={"transcribe": explode},
    )

    assert code == 1
    assert json.loads(events.getvalue())["error_code"] == "INTERNAL_ERROR"
