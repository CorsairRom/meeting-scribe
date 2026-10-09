import struct
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import (
    WavChunkInspector,
)
from meeting_scribe.meetings.infrastructure.wav_mix_reader import WavMixReader


def _chunk(path: Path, left: list[int], right: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(np.column_stack([left, right]).astype("<i2").tobytes())


def _read(meeting: Path):
    timeline = WavChunkInspector().inspect(meeting).unwrap()
    return WavMixReader().read_mix(meeting, timeline)


def test_the_mix_is_the_mean_of_both_channels_scaled_to_float32(tmp_path: Path) -> None:
    _chunk(tmp_path / "audio" / "chunk_00000.wav", [16_384, -16_384, 0], [0, -16_384, 32_767])

    samples = _read(tmp_path).unwrap()

    assert samples.dtype == np.float32
    assert samples.shape == (3,)
    assert samples[:2].tolist() == pytest.approx([0.25, -0.5])
    assert abs(samples[2]) < 0.5  # (0 + 32767) / 2 / 32768: the mean cannot clip


def test_chunks_are_placed_by_their_real_frame_counts(tmp_path: Path) -> None:
    _chunk(tmp_path / "audio" / "chunk_00000.wav", [32_768 // 2] * 5, [32_768 // 2] * 5)
    _chunk(tmp_path / "audio" / "chunk_00001.wav", [-16_384] * 2, [-16_384] * 2)

    samples = _read(tmp_path).unwrap()

    assert samples.shape == (7,)
    assert samples[:5].tolist() == pytest.approx([0.5] * 5)
    assert samples[5:].tolist() == pytest.approx([-0.5] * 2)


def test_a_chunk_whose_header_was_never_patched_is_decoded_from_its_real_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audio" / "chunk_00000.wav"
    _chunk(path, [16_384] * 4, [16_384] * 4)
    raw = bytearray(path.read_bytes())
    raw[40:44] = struct.pack("<I", 0)  # data size as a crashed ffmpeg leaves it
    path.write_bytes(bytes(raw))

    samples = _read(tmp_path).unwrap()

    assert samples.tolist() == pytest.approx([0.5] * 4)


def test_a_chunk_that_shrank_after_inspection_is_a_storage_failure(tmp_path: Path) -> None:
    path = tmp_path / "audio" / "chunk_00000.wav"
    _chunk(path, [1] * 100, [1] * 100)
    timeline = WavChunkInspector().inspect(tmp_path).unwrap()
    path.write_bytes(path.read_bytes()[:60])

    failure = WavMixReader().read_mix(tmp_path, timeline).failure()

    assert failure is not None and failure.error_code == "MEETING_STORAGE_FAILED"
