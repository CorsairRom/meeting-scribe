import tracemalloc
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from meeting_scribe.meetings.infrastructure.wav_channel_reader import (
    WavChannelReader,
    frame_energies,
)
from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import WavChunkInspector


def _chunk(path: Path, left, right) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(np.column_stack([left, right]).astype("<i2").tobytes())


def _timeline(meeting: Path):
    return WavChunkInspector().inspect(meeting).unwrap()


def test_each_channel_is_read_alone_and_scaled_to_float32(tmp_path: Path) -> None:
    _chunk(tmp_path / "audio" / "chunk_00000.wav", [16_384, -16_384, 0], [0, -8_192, 32_767])
    reader, timeline = WavChannelReader(), _timeline(tmp_path)

    mic = reader.read_channel(tmp_path, timeline, "mic").unwrap()
    system = reader.read_channel(tmp_path, timeline, "system").unwrap()

    assert mic.dtype == np.float32 and system.dtype == np.float32
    assert mic.tolist() == pytest.approx([0.5, -0.5, 0.0])
    assert system.tolist() == pytest.approx([0.0, -0.25, 32_767 / 32_768])


def test_chunks_are_placed_by_their_real_frame_counts(tmp_path: Path) -> None:
    _chunk(tmp_path / "audio" / "chunk_00000.wav", [16_384] * 5, [0] * 5)
    _chunk(tmp_path / "audio" / "chunk_00001.wav", [-16_384] * 2, [0] * 2)

    mic = WavChannelReader().read_channel(tmp_path, _timeline(tmp_path), "mic").unwrap()

    assert mic.tolist() == pytest.approx([0.5] * 5 + [-0.5] * 2)


def test_a_chunk_that_shrank_since_it_was_inspected_is_a_storage_error(tmp_path: Path) -> None:
    path = tmp_path / "audio" / "chunk_00000.wav"
    _chunk(path, [1] * 100, [1] * 100)
    timeline = _timeline(tmp_path)
    _chunk(path, [1] * 10, [1] * 10)

    result = WavChannelReader().read_channel(tmp_path, timeline, "mic")

    assert result.failure().error_code == "MEETING_STORAGE_FAILED"


def test_frame_energies_are_mean_squares_per_100_ms() -> None:
    signal = np.concatenate([np.full(1_600, 0.5), np.zeros(1_600), np.full(800, 1.0)]).astype(
        np.float32
    )

    assert frame_energies(signal) == pytest.approx([0.25, 0.0, 1.0])  # the last frame is partial
    assert frame_energies(np.zeros(0, dtype=np.float32)) == []


def test_read_energies_gives_one_envelope_per_channel(tmp_path: Path) -> None:
    _chunk(tmp_path / "audio" / "chunk_00000.wav", [16_384] * 3_200, [0] * 3_200)

    energies = WavChannelReader().read_energies(tmp_path, _timeline(tmp_path)).unwrap()

    assert energies.mic == pytest.approx([0.25, 0.25])
    assert energies.system == [0.0, 0.0]


def _peak_bytes(work) -> int:
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_frame_energies_do_not_copy_the_whole_signal_to_float64() -> None:
    signal = np.random.default_rng(0).uniform(-1, 1, 16_000 * 300).astype(np.float32)

    peak = _peak_bytes(lambda: frame_energies(signal))

    assert peak < signal.nbytes / 4  # blockwise: the old whole-signal float64 copy was 2x


def test_read_energies_never_holds_two_whole_channels(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    for index in range(20):  # 20 chunks of 5 s: 100 s, a channel is 6.4 MB as float32
        samples = rng.integers(-3000, 3000, 80_000)
        _chunk(tmp_path / "audio" / f"chunk_{index:05d}.wav", samples, samples)
    timeline, reader = _timeline(tmp_path), WavChannelReader()
    channel_bytes = timeline.total_frames * 4

    peak = _peak_bytes(lambda: reader.read_energies(tmp_path, timeline).unwrap())

    assert peak < channel_bytes * 1.5  # one channel plus a chunk, not channel + channel
