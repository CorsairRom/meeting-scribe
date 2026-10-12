"""The check on the meetings folder itself (spec section 6, AC-7).

The service never lists or reads the folder: it only creates it when missing and proves it can write
with one temporary file of a fixed neutral name. Messages never hold the path.
"""

import contextlib
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.fields import FieldCode, FieldProblem

KEY = "storage.meetings_dir"
PROBE_NAME = ".meeting-scribe-write-test"


def _invalid(message: str) -> Failure[FieldProblem]:
    return Failure(FieldProblem(KEY, FieldCode.INVALID, message))


def _not_writable() -> Failure[FieldProblem]:
    return Failure(
        FieldProblem(KEY, FieldCode.FOLDER_NOT_WRITABLE, "The meetings folder is not writable.")
    )


def check_meetings_folder(raw: str) -> Result[Path, FieldProblem]:
    path = Path(raw).expanduser()
    if not raw.strip() or not path.is_absolute() or ".." in path.parts:
        return _invalid("The meetings folder must be an absolute path without '..'.")
    if path.exists() and not path.is_dir():
        return _invalid("The meetings folder is not a folder.")
    try:
        if not path.exists():
            if not path.parent.is_dir():
                return _not_writable()
            path.mkdir(mode=0o700)
        probe = path / PROBE_NAME
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            (path / PROBE_NAME).unlink(missing_ok=True)
        return _not_writable()
    return Success(path)
