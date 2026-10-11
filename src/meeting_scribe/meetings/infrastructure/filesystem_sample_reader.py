"""Reads a speaker's voice sample from the meeting folder (served as bytes over D-Bus)."""

import logging
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError

logger = logging.getLogger(__name__)

MAX_SAMPLE_BYTES = 1_048_576
"""Twice the largest sample (15 s of 16 kHz 16-bit mono, about 0.5 MB); far below the bus limit."""


def _not_found() -> Failure[MeetingScribeError]:
    return Failure(
        EntityNotFoundError("The speaker sample is not available.", detail_code="SPEAKER_SAMPLE")
    )


class FilesystemSampleReader:
    def read(self, meeting_dir: Path, relative_path: str) -> Result[bytes, MeetingScribeError]:
        try:
            root = meeting_dir.resolve()
            path = (root / relative_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return _not_found()
            if path.stat().st_size > MAX_SAMPLE_BYTES:
                logger.warning("A speaker sample is larger than %d bytes.", MAX_SAMPLE_BYTES)
                return _not_found()
            return Success(path.read_bytes())
        except OSError:
            logger.warning("A speaker sample could not be read.")
            return _not_found()
