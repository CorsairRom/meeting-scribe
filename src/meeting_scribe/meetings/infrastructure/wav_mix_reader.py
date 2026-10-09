"""Decodes the 2-channel chunks into one mono float32 signal with ``wave`` + numpy (GO-003)."""

import wave
from pathlib import Path

import numpy as np
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.meetings.domain.errors import MeetingStorageError
from meeting_scribe.meetings.domain.timeline import CHANNELS, SAMPLE_WIDTH_BYTES, Timeline

_FRAME_BYTES = CHANNELS * SAMPLE_WIDTH_BYTES
_SCALE = 0.5 / 32768  # mean of two int16 channels, to [-1, 1)


class WavMixReader:
    def read_mix(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[Samples, MeetingStorageError]:
        """Decode chunk by chunk into one preallocated array (no ``concatenate``, spec 2.3).

        The timeline's frame count rules, not the header: a chunk a crash left with an unpatched
        header is read for as many frames as the file really holds.
        """
        mix = np.zeros(timeline.total_frames, dtype=np.float32)
        position = 0
        try:
            for chunk in timeline.chunks:
                raw = read_frames(meeting_dir / "audio" / chunk.name, chunk.frames)
                if len(raw) < chunk.frames * _FRAME_BYTES:
                    return Failure(
                        MeetingStorageError(f"Chunk '{chunk.name}' is shorter than when inspected.")
                    )
                pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, CHANNELS)
                block = mix[position : position + chunk.frames]
                np.add(pcm[:, 0], pcm[:, 1], out=block, dtype=np.float32)
                block *= _SCALE
                position += chunk.frames
        except (OSError, EOFError, wave.Error) as error:
            return Failure(MeetingStorageError(f"Cannot read the audio chunks: {error}"))
        return Success(mix)


def read_frames(path: Path, frames: int) -> bytes:
    """Up to ``frames`` frames of raw PCM, from the first sample (past any header)."""
    with path.open("rb") as handle:
        with wave.open(handle):  # parses the header; leaves the handle at the first sample
            data_offset = handle.tell()
        handle.seek(data_offset)
        return handle.read(frames * _FRAME_BYTES)
