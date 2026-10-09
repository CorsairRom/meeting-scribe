import io
import json
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.worker import build_diarize_stage, main
from tests.unit.speakers.fakes import PARAMS, FakeBackend


def _meeting(tmp_path: Path, system_amplitude: float) -> Path:
    """Four seconds on the system channel: a tone from 1 s to 3 s (or silence: in person).

    The activity test is relative to the channel's own noise floor, so some frames must be quiet.
    """
    audio = tmp_path / "audio"
    audio.mkdir()
    seconds = np.arange(64_000) / 16_000
    system = system_amplitude * np.sin(2 * np.pi * 440 * seconds) * ((seconds >= 1) & (seconds < 3))
    pcm = np.column_stack([np.zeros(64_000), system * 32_767]).astype("<i2")
    with wave.open(str(audio / "chunk_00000.wav"), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(pcm.tobytes())
    return tmp_path


def _spec(meeting: Path, device: str = "cuda") -> str:
    return json.dumps(
        {
            "protocol": 1,
            "stage": "diarize",
            "meeting_dir": str(meeting),
            "device": device,
            "transcription": {},
            "speakers": PARAMS.model_dump(),
        }
    )


def _drive(meeting: Path, backend: FakeBackend, device: str = "cuda"):
    events = io.StringIO()
    code = main(
        ["diarize"],
        stdin=io.StringIO(_spec(meeting, device)),
        events=events,
        stages={"diarize": lambda: build_diarize_stage(backend)},
    )
    return code, [json.loads(line) for line in events.getvalue().splitlines()]


def test_the_diarize_stage_runs_end_to_end_with_a_fake_pipeline(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.5)
    backend = FakeBackend()
    backend.steps = (("segmentation", 2, 1),)

    code, events = _drive(meeting, backend)

    assert code == 0
    assert [e["event"] for e in events] == ["started", "progress", "result"]
    assert events[0]["device"] == "cuda"
    assert events[1]["stage"] == "diarize"
    assert events[-1]["artifacts"] == {"turns": "turns.json"}
    cache = json.loads((meeting / "turns.json").read_text(encoding="utf-8"))
    assert cache["key"]["params"]["channel"] == "system"
    assert [t["speaker"] for t in cache["data"]["exclusive_diarization"]] == [
        "SPEAKER_00",
        "SPEAKER_01",
    ]
    waveform = backend.calls[0][0]["waveform"]
    assert waveform.shape == (64_000,)  # type: ignore[union-attr]


def test_a_silent_system_channel_diarizes_the_microphone(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.0)

    code, _ = _drive(meeting, FakeBackend())

    assert code == 0
    cache = json.loads((meeting / "turns.json").read_text(encoding="utf-8"))
    assert cache["key"]["params"]["channel"] == "mic"


def test_a_second_run_is_a_cache_hit_and_never_builds_a_pipeline(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.5)
    _drive(meeting, FakeBackend())
    backend = FakeBackend()

    code, events = _drive(meeting, backend)

    assert code == 0
    assert [e["event"] for e in events] == ["cache_hit", "result"]
    assert backend.events == []


def test_cuda_without_a_device_is_one_gpu_unavailable_error_and_no_turns(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.5)
    backend = FakeBackend()
    backend.cuda_devices = 0

    code, events = _drive(meeting, backend)

    assert code == 1
    assert events[-1]["error_code"] == "GPU_UNAVAILABLE"
    assert "PROCESSING_DEVICE=cpu" in events[-1]["message"]
    assert not (meeting / "turns.json").exists()


def test_an_invalid_speakers_section_is_an_invalid_value_error(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.5)
    spec = json.loads(_spec(meeting))
    spec["speakers"] = {"pipeline": ""}
    events = io.StringIO()

    code = main(
        ["diarize"],
        stdin=io.StringIO(json.dumps(spec)),
        events=events,
        stages={"diarize": lambda: build_diarize_stage(FakeBackend())},
    )

    assert code == 1
    assert json.loads(events.getvalue())["error_code"] == "INVALID_VALUE"
