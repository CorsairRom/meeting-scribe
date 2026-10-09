"""Session timeline and audio fingerprint, built from WAV header facts (pure, no I/O)."""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
)

SAMPLE_RATE = 16_000
CHANNELS = 2
SAMPLE_WIDTH_BYTES = 2

type Fingerprint = tuple[tuple[str, int, int], ...]
"""``((file name, frames, size in bytes), ...)`` in chunk order; part of every cache key."""


class ChunkHeader(BaseModel):
    """What the WAV header says about one chunk file."""

    model_config = ConfigDict(frozen=True)

    name: str
    frames: int
    size_bytes: int
    channels: int
    sample_rate: int
    sample_width_bytes: int


class TimelineChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    offset_seconds: float
    frames: int
    size_bytes: int


class Timeline(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunks: tuple[TimelineChunk, ...]

    @property
    def total_frames(self) -> int:
        return sum(chunk.frames for chunk in self.chunks)

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / SAMPLE_RATE


def build_timeline(
    headers: Sequence[ChunkHeader],
) -> Result[Timeline, MeetingAudioMissingError | MeetingAudioUnsupportedError]:
    """Offsets come from the frame count of every previous chunk, never from ``index * 30``.

    Chunks without frames are ignored; chunks are ordered by file name (their index).
    """
    audible = sorted((h for h in headers if h.frames > 0), key=lambda h: h.name)
    if not audible:
        return Failure(MeetingAudioMissingError("The recording has no chunk with audio frames."))
    for header in audible:
        if (header.channels, header.sample_rate, header.sample_width_bytes) != (
            CHANNELS,
            SAMPLE_RATE,
            SAMPLE_WIDTH_BYTES,
        ):
            return Failure(
                MeetingAudioUnsupportedError(
                    f"Chunk '{header.name}' is {header.channels} channel(s), "
                    f"{header.sample_rate} Hz, "
                    f"{header.sample_width_bytes * 8}-bit; expected {CHANNELS} channels, "
                    f"{SAMPLE_RATE} Hz, {SAMPLE_WIDTH_BYTES * 8}-bit."
                )
            )
    chunks: list[TimelineChunk] = []
    elapsed_frames = 0
    for header in audible:
        chunks.append(
            TimelineChunk(
                name=header.name,
                offset_seconds=elapsed_frames / SAMPLE_RATE,
                frames=header.frames,
                size_bytes=header.size_bytes,
            )
        )
        elapsed_frames += header.frames
    return Success(Timeline(chunks=tuple(chunks)))


def audio_fingerprint(timeline: Timeline) -> Fingerprint:
    return tuple((c.name, c.frames, c.size_bytes) for c in timeline.chunks)
