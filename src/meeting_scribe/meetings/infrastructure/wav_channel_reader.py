"""Decodes one channel of the 2-channel chunks, and its frame energies (spec 2.3, GO-003)."""

import wave
from pathlib import Path
from typing import Literal

import numpy as np
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import SAMPLE_RATE, Samples
from meeting_scribe.meetings.domain.errors import MeetingStorageError
from meeting_scribe.meetings.domain.ports import ChannelEnergies
from meeting_scribe.meetings.domain.timeline import CHANNELS, SAMPLE_WIDTH_BYTES, Timeline
from meeting_scribe.meetings.infrastructure.wav_mix_reader import read_frames

_FRAME_BYTES = CHANNELS * SAMPLE_WIDTH_BYTES
_SCALE = 1 / 32768
_ENERGY_FRAME_SAMPLES = SAMPLE_RATE // 10  # 100 ms, spec 2.4
_INDEX = {"mic": 0, "system": 1}
_BLOCK_FRAMES = 128  # energy frames squared at a time: 128 x 1 600 samples x 8 B = 1.6 MB


class WavChannelReader:
    def read_channel(
        self, meeting_dir: Path, timeline: Timeline, channel: Literal["mic", "system"]
    ) -> Result[Samples, MeetingStorageError]:
        """Decode chunk by chunk into one preallocated array (no ``concatenate``, spec 2.3)."""
        signal = np.zeros(timeline.total_frames, dtype=np.float32)
        position = 0
        try:
            for chunk in timeline.chunks:
                raw = read_frames(meeting_dir / "audio" / chunk.name, chunk.frames)
                if len(raw) < chunk.frames * _FRAME_BYTES:
                    return Failure(
                        MeetingStorageError(f"Chunk '{chunk.name}' is shorter than when inspected.")
                    )
                pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, CHANNELS)
                block = signal[position : position + chunk.frames]
                np.multiply(pcm[:, _INDEX[channel]], _SCALE, out=block, dtype=np.float32)
                position += chunk.frames
        except (OSError, EOFError, wave.Error) as error:
            return Failure(MeetingStorageError(f"Cannot read the audio chunks: {error}"))
        return Success(signal)

    def read_energies(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[ChannelEnergies, MeetingStorageError]:
        envelopes: dict[str, list[float]] = {}
        for channel in ("mic", "system"):
            signal = self.read_channel(meeting_dir, timeline, channel)
            if isinstance(signal, Failure):
                return signal
            envelopes[channel] = frame_energies(signal.unwrap())
            del signal  # drop this channel before the next one is decoded: one at a time
        return Success(ChannelEnergies(mic=envelopes["mic"], system=envelopes["system"]))


def frame_energies(signal: Samples) -> list[float]:
    """Mean square per 100 ms frame; the last frame averages only the samples it has.

    Computed in blocks so the float64 working copy stays a few MB whatever the meeting length
    (a whole-signal copy plus its square was about 1 GB per meeting-hour).
    """
    if not len(signal):
        return []
    whole = len(signal) // _ENERGY_FRAME_SAMPLES
    energies: list[float] = []
    squares = np.empty(_BLOCK_FRAMES * _ENERGY_FRAME_SAMPLES, dtype=np.float64)  # reused
    for first in range(0, whole, _BLOCK_FRAMES):
        frames = min(_BLOCK_FRAMES, whole - first)
        block = signal[first * _ENERGY_FRAME_SAMPLES : (first + frames) * _ENERGY_FRAME_SAMPLES]
        work = squares[: len(block)]
        np.multiply(block, block, out=work, dtype=np.float64)
        energies.extend(work.reshape(frames, _ENERGY_FRAME_SAMPLES).mean(axis=1).tolist())
    tail = signal[whole * _ENERGY_FRAME_SAMPLES :]
    if len(tail):
        energies.append(_mean_square(tail))
    return energies


def _mean_square(samples: Samples) -> float:
    return float(np.mean(np.square(samples, dtype=np.float64)))
