"""``utterances.json`` read and ``transcript.md`` rewrite for review edits (review-app spec 2).

No numpy: this adapter runs in the service, not in the worker. Errors never quote the file content,
which is private meeting text.
"""

import json
import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError
from meeting_scribe.speakers.domain.errors import TranscriptCorruptError, TranscriptStorageError
from meeting_scribe.speakers.domain.transcript import TRANSCRIPT_FILE, UTTERANCES_FILE
from meeting_scribe.speakers.domain.turns import Utterance

_UTTERANCES = TypeAdapter(tuple[Utterance, ...])


class JsonUtterancesStore:
    def load(
        self, meeting_dir: Path
    ) -> Result[tuple[Utterance, ...], EntityNotFoundError | TranscriptCorruptError]:
        try:
            document = json.loads((meeting_dir / UTTERANCES_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Failure(
                EntityNotFoundError("The meeting has no transcript.", detail_code="TRANSCRIPT")
            )
        except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8
            return Failure(TranscriptCorruptError(f"{UTTERANCES_FILE} is unreadable."))
        try:
            return Success(_UTTERANCES.validate_python(document))
        except ValidationError:  # its text would quote the private content
            return Failure(TranscriptCorruptError(f"{UTTERANCES_FILE} has an invalid shape."))

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, TranscriptStorageError]:
        target = meeting_dir / TRANSCRIPT_FILE
        temporary = meeting_dir / f".{TRANSCRIPT_FILE}.tmp"
        try:
            with temporary.open("wb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)
            return Failure(TranscriptStorageError(f"Cannot write {TRANSCRIPT_FILE}."))
        return Success(TRANSCRIPT_FILE)
