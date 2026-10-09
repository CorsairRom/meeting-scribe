"""``turns.json`` on disk: atomic writes, and a damaged file reads as 'no cache' (spec 4)."""

import json
import logging
import os
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.speakers.domain.diarization import TURNS_FILE
from meeting_scribe.speakers.domain.errors import DiarizationError

logger = logging.getLogger(__name__)


class JsonTurnsCache:
    def load(self, meeting_dir: Path) -> dict[str, object]:
        path = meeting_dir / TURNS_FILE
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:  # ValueError covers bad JSON and bad UTF-8
            logger.warning("Ignoring unreadable %s (%s); diarizing again.", TURNS_FILE, error)
            return {}
        return document if isinstance(document, dict) else {}

    def store(
        self, meeting_dir: Path, document: dict[str, object]
    ) -> Result[None, DiarizationError]:
        target = meeting_dir / TURNS_FILE
        temporary = meeting_dir / f".{TURNS_FILE}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            return Failure(DiarizationError(f"Cannot write {TURNS_FILE}: {error}"))
        return Success(None)
