"""Ports of the preferences domain."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from returns.result import Result

from meeting_scribe.preferences.domain.errors import (
    SettingsFileInvalidError,
    SettingsRestartUnavailableError,
    SettingsStorageFailedError,
)
from meeting_scribe.preferences.domain.fields import FieldSource


class SettingsFileStore(Protocol):
    """The sparse key to string values the user saved (never a secret, BR-005)."""

    def read(self) -> Result[dict[str, str], SettingsFileInvalidError]:
        """No file reads as no values; a damaged file is a failure, never an exception."""
        ...

    def write(self, values: Mapping[str, str]) -> Result[None, SettingsStorageFailedError]:
        """Replace the whole file atomically; a damaged old file is moved aside first."""
        ...


class EffectiveSettings(Protocol):
    """The settings in effect, as the use cases see them (the root ``Settings`` satisfies it)."""

    def exposed_value(self, key: str) -> str: ...

    def exposed_default(self, key: str) -> str: ...

    def source_of(self, key: str) -> FieldSource: ...


class ActivityProbe(Protocol):
    """Whether a recording, a processing job or a summary is active right now."""

    def busy(self) -> bool: ...


class ServiceRestarter(Protocol):
    """Asks the user systemd manager to restart the service unit (never done in tests)."""

    async def is_reachable(self) -> bool: ...

    async def restart(self) -> Result[None, SettingsRestartUnavailableError]: ...


class SettingsEvents(Protocol):
    """Observer of saved settings; called on the thread that saved them."""

    def changed(self, keys: Sequence[str]) -> None: ...

    def restart_required_changed(self, keys: Sequence[str]) -> None: ...


class NullSettingsEvents:
    def changed(self, keys: Sequence[str]) -> None:
        pass

    def restart_required_changed(self, keys: Sequence[str]) -> None:
        pass
