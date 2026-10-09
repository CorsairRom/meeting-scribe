"""``words.json`` on disk: atomic writes, and a damaged file reads as 'no cache' (spec 4)."""

import json
import logging
import os
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.transcription.domain.cache import WORDS_FILE
from meeting_scribe.transcription.domain.errors import TranscriptionError

logger = logging.getLogger(__name__)


class JsonWordsCache:
    def load(self, meeting_dir: Path) -> dict[str, object]:
        path = meeting_dir / WORDS_FILE
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:  # ValueError covers bad JSON and bad UTF-8
            logger.warning("Ignoring unreadable %s (%s); transcribing again.", WORDS_FILE, error)
            return {}
        return document if isinstance(document, dict) else {}

    def store(
        self, meeting_dir: Path, document: dict[str, object]
    ) -> Result[None, TranscriptionError]:
        target = meeting_dir / WORDS_FILE
        temporary = meeting_dir / f".{WORDS_FILE}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            return Failure(TranscriptionError(f"Cannot write {WORDS_FILE}: {error}"))
        return Success(None)
