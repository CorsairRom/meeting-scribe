"""Ports of the preferences domain."""

from collections.abc import Mapping
from typing import Protocol

from returns.result import Result

from meeting_scribe.preferences.domain.errors import (
    SettingsFileInvalidError,
    SettingsStorageFailedError,
)


class SettingsFileStore(Protocol):
    """The sparse key to string values the user saved (never a secret, BR-005)."""

    def read(self) -> Result[dict[str, str], SettingsFileInvalidError]:
        """No file reads as no values; a damaged file is a failure, never an exception."""
        ...

    def write(self, values: Mapping[str, str]) -> Result[None, SettingsStorageFailedError]:
        """Replace the whole file atomically; a damaged old file is moved aside first."""
        ...
