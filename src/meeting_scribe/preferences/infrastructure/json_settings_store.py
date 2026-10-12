"""``settings.json``: ``{"version": 1, "values": {...}}``, written atomically with mode 0600.

Same write pattern as ``meeting.json`` (ADR-004): temporary file in the folder, ``fsync``, then
``os.replace``. Messages and logs carry keys and codes only: a value can hold a home path.
"""

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.errors import (
    SettingsFileInvalidError,
    SettingsStorageFailedError,
)

logger = logging.getLogger(__name__)

FILE_VERSION = 1
INVALID_SUFFIX = ".invalid"


class JsonSettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> Result[dict[str, str], SettingsFileInvalidError]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Success({})
        except (OSError, UnicodeDecodeError):
            return Failure(SettingsFileInvalidError("The settings file could not be read."))
        try:
            document = json.loads(raw)
        except ValueError:
            return Failure(SettingsFileInvalidError("The settings file is not valid JSON."))
        if not isinstance(document, dict) or document.get("version") != FILE_VERSION:
            return Failure(SettingsFileInvalidError("The settings file has an unknown version."))
        stored = document.get("values")
        if not isinstance(stored, dict):
            return Failure(SettingsFileInvalidError("The settings file has no values section."))
        values: dict[str, str] = {}
        for key, value in stored.items():
            if isinstance(value, str):
                values[str(key)] = value
            else:
                logger.warning("Settings file: ignored key %s (not a string).", key)
        return Success(values)

    def write(self, values: Mapping[str, str]) -> Result[None, SettingsStorageFailedError]:
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._set_aside_a_damaged_file()
            self._replace(values)
        except OSError:
            logger.error("Settings file could not be written: SETTINGS_STORAGE_FAILED")
            return Failure(
                SettingsStorageFailedError(
                    "The settings file could not be written. "
                    "Check disk space and the permissions of the config folder."
                )
            )
        return Success(None)

    def _set_aside_a_damaged_file(self) -> None:
        if isinstance(self.read(), Failure):
            self._path.replace(self._path.with_name(self._path.name + INVALID_SUFFIX))

    def _replace(self, values: Mapping[str, str]) -> None:
        document = {"version": FILE_VERSION, "values": dict(sorted(values.items()))}
        descriptor, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=".settings-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
