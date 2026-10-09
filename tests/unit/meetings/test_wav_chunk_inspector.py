import logging
import struct
import wave
from pathlib import Path

import pytest

from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import WavChunkInspector


def _chunk(meeting: Path, index: int, frames: int, channels: int = 2, rate: int = 16_000) -> Path:
    audio = meeting / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    path = audio / f"chunk_{index:05d}.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * channels * frames)
    return path


def test_offsets_use_the_real_frame_count_of_every_chunk(tmp_path: Path) -> None:
    _chunk(tmp_path, 0, 480_640)
    _chunk(tmp_path, 1, 480_320)
    _chunk(tmp_path, 2, 12_000)

    timeline = WavChunkInspector().inspect(tmp_path).unwrap()

    assert [c.offset_seconds for c in timeline.chunks] == [0.0, 30.04, 60.06]
    assert round(timeline.duration_seconds, 2) == 60.81


def test_no_audio_folder_or_no_frames_is_missing_audio(tmp_path: Path) -> None:
    assert isinstance(WavChunkInspector().inspect(tmp_path).failure(), MeetingAudioMissingError)
    _chunk(tmp_path, 0, 0)
    assert isinstance(WavChunkInspector().inspect(tmp_path).failure(), MeetingAudioMissingError)


def test_a_mono_chunk_is_unsupported_audio(tmp_path: Path) -> None:
    _chunk(tmp_path, 0, 1000, channels=1)

    assert isinstance(WavChunkInspector().inspect(tmp_path).failure(), MeetingAudioUnsupportedError)


def test_a_truncated_last_chunk_recovers_its_frames_from_the_file_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _chunk(tmp_path, 0, 1000)
    crashed = _chunk(tmp_path, 1, 500)
    data = bytearray(crashed.read_bytes())
    data[40:44] = struct.pack("<I", 0)  # a killed ffmpeg never patched the data size
    crashed.write_bytes(bytes(data))

    with caplog.at_level(logging.WARNING):
        timeline = WavChunkInspector().inspect(tmp_path).unwrap()

    assert [c.frames for c in timeline.chunks] == [1000, 500]
    assert "chunk_00001.wav" in caplog.text


def test_files_that_are_not_chunks_are_ignored(tmp_path: Path) -> None:
    _chunk(tmp_path, 0, 100)
    (tmp_path / "audio" / "notes.txt").write_text("x", encoding="utf-8")

    assert len(WavChunkInspector().inspect(tmp_path).unwrap().chunks) == 1


@pytest.mark.parametrize("keep", [5, 14, 20, 30, 40])
def test_a_chunk_cut_inside_its_header_is_a_storage_failure_not_a_crash(
    tmp_path: Path, keep: int
) -> None:
    path = _chunk(tmp_path, 0, 100)
    path.write_bytes(path.read_bytes()[:keep])

    failure = WavChunkInspector().inspect(tmp_path).failure()

    assert isinstance(failure, MeetingStorageError)


def test_a_garbage_chunk_is_a_storage_failure(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "chunk_00000.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\xff" * 7)

    assert isinstance(WavChunkInspector().inspect(tmp_path).failure(), MeetingStorageError)
