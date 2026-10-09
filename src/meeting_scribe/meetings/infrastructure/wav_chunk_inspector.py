"""Reads the WAV headers of ``<meeting>/audio/chunk_*.wav`` into a timeline (spec 2.1)."""

import logging
import struct
from pathlib import Path

from returns.result import Failure, Result

from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.domain.timeline import ChunkHeader, Timeline, build_timeline

logger = logging.getLogger(__name__)

_UNPATCHED_SIZES = (0, 0xFFFFFFFF)


class WavChunkInspector:
    def inspect(
        self, meeting_dir: Path
    ) -> Result[
        Timeline, MeetingAudioMissingError | MeetingAudioUnsupportedError | MeetingStorageError
    ]:
        audio = meeting_dir / "audio"
        try:
            paths = sorted(audio.glob("chunk_*.wav")) if audio.is_dir() else []
            headers = [self._header(path) for path in paths]
        except (OSError, ValueError) as error:
            return Failure(MeetingStorageError(f"Cannot read the audio chunks: {error}"))
        return build_timeline(headers)

    def _header(self, path: Path) -> ChunkHeader:
        """Walk the RIFF chunks; recover the frame count from the file size when the header lies."""
        size = path.stat().st_size
        with path.open("rb") as handle:
            if handle.read(12)[8:12] != b"WAVE":
                raise ValueError(f"{path.name} is not a WAV file")
            channels = rate = width = 0
            while chunk := handle.read(8):
                chunk_id, chunk_size = struct.unpack("<4sI", chunk)
                if chunk_id == b"fmt ":
                    _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", handle.read(16))
                    width = bits // 8
                    handle.seek(chunk_size - 16, 1)
                elif chunk_id == b"data":
                    data_offset = handle.tell()
                    return self._with_frames(
                        path, size, data_offset, chunk_size, channels, rate, width
                    )
                else:
                    handle.seek(chunk_size + (chunk_size & 1), 1)
        raise ValueError(f"{path.name} has no data chunk")

    @staticmethod
    def _with_frames(
        path: Path, size: int, offset: int, declared: int, channels: int, rate: int, width: int
    ) -> ChunkHeader:
        block_align = max(channels * width, 1)
        actual = max(size - offset, 0)
        if declared in _UNPATCHED_SIZES or declared > actual:
            logger.warning(
                "Chunk '%s' is truncated; frames recovered from the file size.", path.name
            )
            declared = actual
        return ChunkHeader(
            name=path.name,
            frames=declared // block_align,
            size_bytes=size,
            channels=channels,
            sample_rate=rate,
            sample_width_bytes=width,
        )
