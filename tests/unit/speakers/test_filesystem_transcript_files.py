import json
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.speakers.domain.samples import Clip
from meeting_scribe.speakers.domain.turns import SpeakerLabel, Utterance
from meeting_scribe.speakers.infrastructure.filesystem_transcript_files import (
    FilesystemTranscriptFiles,
)

RATE = 16_000


def _tone(seconds: float, amplitude: float) -> "np.ndarray":
    t = np.arange(round(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_a_voice_sample_is_mono_16_bit_with_gaps_and_a_minus_3_dbfs_peak(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.reset_samples(tmp_path).unwrap()
    signal = np.concatenate([_tone(6, 0.1), _tone(4, 0.1), _tone(2, 0.1)])
    clips = [Clip(start=0, end=6), Clip(start=6, end=10), Clip(start=10, end=12)]

    path = files.write_sample(tmp_path, "S1", signal, clips).unwrap()

    assert path == "samples/S1.wav"
    with wave.open(str(tmp_path / path)) as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, RATE)
        assert reader.getnframes() / RATE == pytest.approx(12.8)  # 6 + 4 + 2 + 2 x 0.4 s
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    assert pcm.max() / 32767 == pytest.approx(10 ** (-3 / 20), abs=0.002)
    gap = pcm[6 * RATE : 6 * RATE + round(0.4 * RATE)]
    assert not gap.any()  # silence between clips


def test_a_silent_signal_is_written_without_dividing_by_zero(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.reset_samples(tmp_path).unwrap()

    files.write_sample(tmp_path, "ME", np.zeros(RATE * 3, dtype=np.float32), [Clip(start=0, end=2)])

    with wave.open(str(tmp_path / "samples" / "ME.wav")) as reader:
        assert not any(reader.readframes(reader.getnframes()))


def test_reset_removes_samples_of_a_previous_run(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.reset_samples(tmp_path).unwrap()
    (tmp_path / "samples" / "S9.wav").write_bytes(b"old")

    files.reset_samples(tmp_path).unwrap()

    assert list((tmp_path / "samples").iterdir()) == []


def test_utterances_and_transcript_are_written_utf_8_atomically(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    labels = {"SPEAKER_07": SpeakerLabel(id="S1", label="Speaker 1")}
    utterances = [Utterance(speaker="SPEAKER_07", start=1.0, end=2.0, text="año nuevo")]

    assert files.write_utterances(tmp_path, utterances, labels).unwrap() == "utterances.json"
    assert files.write_transcript(tmp_path, "[00:00:01] **Speaker 1:** año nuevo\n").unwrap() == (
        "transcript.md"
    )

    document = json.loads((tmp_path / "utterances.json").read_text(encoding="utf-8"))
    assert document == [{"speaker": "S1", "start": 1.0, "end": 2.0, "text": "año nuevo"}]
    assert "año" in (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".*.tmp"))


def test_a_write_failure_is_a_diarization_error(tmp_path: Path) -> None:
    result = FilesystemTranscriptFiles().write_transcript(tmp_path / "missing", "x")

    assert result.failure().error_code == "DIARIZATION_FAILED"
    assert FilesystemTranscriptFiles().reset_samples(tmp_path / "missing").failure().error_code == (
        "DIARIZATION_FAILED"
    )
