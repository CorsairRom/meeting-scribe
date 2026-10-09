import io
import json
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.worker import build_diarize_stage, main
from tests.unit.speakers.fakes import PARAMS, SPEAKERS_SPEC, FakeBackend

SPEAKERS = {**PARAMS.model_dump(), **SPEAKERS_SPEC}


def _meeting(tmp_path: Path, system_amplitude: float, words: bool = True) -> Path:
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
    if words:
        _words(tmp_path, [(1.2, 1.6, "hola"), (1.7, 2.1, "mundo")])
    return tmp_path


def _words(meeting: Path, words: list[tuple[float, float, str]]) -> None:
    document = {
        "cache_version": 1,
        "key": {},
        "data": {
            "detected_language": "es",
            "words": [{"start": a, "end": b, "text": t, "probability": 1.0} for a, b, t in words],
        },
    }
    (meeting / "words.json").write_text(json.dumps(document), encoding="utf-8")


def _spec(meeting: Path, device: str = "cuda") -> str:
    return json.dumps(
        {
            "protocol": 1,
            "stage": "diarize",
            "meeting_dir": str(meeting),
            "device": device,
            "transcription": {},
            "speakers": SPEAKERS,
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
    backend.turns = backend.exclusive  # nobody overlaps: every turn is clean

    code, events = _drive(meeting, backend)

    assert code == 0
    assert [e["event"] for e in events] == ["started", "progress", "stage", "result"]
    assert events[0]["device"] == "cuda"
    assert events[1]["stage"] == "diarize"
    assert events[2]["state"] == "EXTRACTING_SAMPLES"
    result = events[-1]
    assert result["artifacts"] == {
        "turns": "turns.json",
        "utterances": "utterances.json",
        "transcript": "transcript.md",
    }
    assert result["detected_language"] == "es"
    assert [(s["id"], s["label"], s["utterance_count"]) for s in result["speakers"]] == [
        ("S1", "Speaker 1", 1)
    ]
    cache = json.loads((meeting / "turns.json").read_text(encoding="utf-8"))
    assert cache["key"]["params"]["channel"] == "system"
    assert [t["speaker"] for t in cache["data"]["exclusive_diarization"]] == [
        "SPEAKER_00",
        "SPEAKER_01",
    ]
    assert (meeting / "transcript.md").read_text(encoding="utf-8") == (
        "[00:00:01] **Speaker 1:** hola mundo\n"
    )
    assert result["speakers"][0]["sample"] == "samples/S1.wav"
    with wave.open(str(meeting / "samples" / "S1.wav")) as sample:
        assert (sample.getnchannels(), sample.getframerate()) == (1, 16_000)
        assert sample.getnframes() / 16_000 == pytest.approx(3.0)  # the clean 0-3 s turn
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
    assert [e["event"] for e in events] == ["cache_hit", "stage", "result"]
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


def test_a_meeting_without_words_fails_before_any_pipeline_is_built(tmp_path: Path) -> None:
    meeting = _meeting(tmp_path, 0.5, words=False)
    backend = FakeBackend()

    code, events = _drive(meeting, backend)

    assert code == 1
    assert [e["event"] for e in events] == ["error"]
    assert events[0]["error_code"] == "TRANSCRIPTION_FAILED"
    assert backend.events == []  # nothing was prepared, loaded or run
    assert not (meeting / "turns.json").exists()
