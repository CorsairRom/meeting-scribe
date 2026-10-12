"""The check on the meetings folder itself (spec section 6, AC-7).

``check_meetings_folder`` has no side effect on a missing folder, so a rejected ``Set`` creates
nothing; ``create_meetings_folder`` runs only when the whole ``Set`` will be applied. The service
never lists or reads the folder: it proves it can write with one temporary file of a fixed
neutral name. Messages never hold the path.
"""

import contextlib
import os
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY, FieldCode, FieldProblem

KEY = next(spec.key for spec in FIELD_REGISTRY if spec.kind == "path")
PROBE_NAME = ".meeting-scribe-write-test"


def _invalid(message: str) -> Failure[FieldProblem]:
    return Failure(FieldProblem(KEY, FieldCode.INVALID, message))


def _not_writable() -> Failure[FieldProblem]:
    return Failure(
        FieldProblem(KEY, FieldCode.FOLDER_NOT_WRITABLE, "The meetings folder is not writable.")
    )


def check_meetings_folder(raw: str) -> Result[Path, FieldProblem]:
    """Absolute, no ``..``, not a file, and writable (or creatable under a writable parent)."""
    if "\x00" in raw:
        return _invalid("The meetings folder is not a valid path.")
    try:
        path = Path(raw).expanduser()
        if not raw.strip() or not path.is_absolute() or ".." in path.parts:
            return _invalid("The meetings folder must be an absolute path without '..'.")
        if path.exists():
            return (
                _probe(path) if path.is_dir() else _invalid("The meetings folder is not a folder.")
            )
        parent_ok = path.parent.is_dir() and os.access(path.parent, os.W_OK | os.X_OK)
        return Success(path) if parent_ok else _not_writable()
    except (RuntimeError, ValueError):  # an unknown ``~user``; a NUL byte
        return _invalid("The meetings folder is not a valid path.")
    except OSError:
        return _not_writable()


def create_meetings_folder(raw: str) -> Result[Path, FieldProblem]:
    """Check, then create the folder (mode 0700) when it is missing, then prove it is writable."""
    checked = check_meetings_folder(raw)
    if isinstance(checked, Failure):
        return checked
    path = checked.unwrap()
    if not path.exists():
        try:
            path.mkdir(mode=0o700)
        except OSError:
            return _not_writable()
    return _probe(path)


def _probe(path: Path) -> Result[Path, FieldProblem]:
    probe = path / PROBE_NAME
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)
        return _not_writable()
    return Success(path)


class MeetingsFolderPreparer:
    """``create_meetings_folder`` that remembers a folder it made, so a failed save can undo it."""

    def __init__(self) -> None:
        self._created: Path | None = None

    def __call__(self, raw: str) -> Result[Path, FieldProblem]:
        self._created = None
        checked = check_meetings_folder(raw)
        if isinstance(checked, Failure):
            return checked
        existed = checked.unwrap().exists()
        created = create_meetings_folder(raw)
        if isinstance(created, Success) and not existed:
            self._created = created.unwrap()
        return created

    def commit(self) -> None:
        """The settings file was written: the folder is now wanted, so forget it was new."""
        self._created = None

    def rollback(self) -> None:
        """Remove the folder this preparer made, only while it is still empty."""
        created, self._created = self._created, None
        if created is not None:
            with contextlib.suppress(OSError):
                created.rmdir()
