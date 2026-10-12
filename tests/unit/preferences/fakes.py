"""Fakes for the preferences ports: memory only, no file, no bus, no systemd."""

import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.application.settings_service import (
    PreferencesService,
    PreferencesState,
)
from meeting_scribe.preferences.domain.errors import (
    SettingsFileInvalidError,
    SettingsRestartUnavailableError,
    SettingsStorageFailedError,
)
from meeting_scribe.preferences.domain.fields import FieldProblem
from meeting_scribe.preferences.domain.ports import EffectiveSettings
from meeting_scribe.preferences.infrastructure.meetings_folder import (
    MeetingsFolderPreparer,
    check_meetings_folder,
)
from meeting_scribe.settings import Settings


class MemoryStore:
    """A ``SettingsFileStore`` in memory; records every write and can fail or block."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})
        self.writes: list[dict[str, str]] = []
        self.fail = False
        self.on_write: Callable[[], None] | None = None
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.write_calls: list[str] = []  # the language of each write, at entry
        self._inside = 0
        self.max_concurrent = 0

    def read(self) -> Result[dict[str, str], SettingsFileInvalidError]:
        return Success(dict(self.values))

    def write(self, values: Mapping[str, str]) -> Result[None, SettingsStorageFailedError]:
        self.write_calls.append(values.get("summaries.language", ""))
        self._inside += 1
        self.max_concurrent = max(self.max_concurrent, self._inside)
        self.entered.set()
        try:
            return self._write(values)
        finally:
            self._inside -= 1

    def _write(self, values: Mapping[str, str]) -> Result[None, SettingsStorageFailedError]:
        if self.on_write is not None:
            self.on_write()
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            return Failure(SettingsStorageFailedError("The settings file could not be written."))
        self.values = dict(values)
        self.writes.append(dict(values))
        return Success(None)


class RecordingEvents:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.log: list[tuple[str, tuple[str, ...]]] = []
        self.writes_seen: list[int] = []
        self._store = store

    def changed(self, keys: Sequence[str]) -> None:
        self.log.append(("changed", tuple(keys)))
        if self._store is not None:
            self.writes_seen.append(len(self._store.writes))

    def restart_required_changed(self, keys: Sequence[str]) -> None:
        self.log.append(("restart_required", tuple(keys)))


class FakeRestarter:
    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.restarts = 0
        self.fail = False
        self.explode = False

    async def is_reachable(self) -> bool:
        if self.explode:
            raise RuntimeError("bus exploded")
        return self.reachable

    async def restart(self) -> Result[None, SettingsRestartUnavailableError]:
        self.restarts += 1
        if self.explode:
            raise RuntimeError("bus exploded")
        if self.fail:
            return Failure(SettingsRestartUnavailableError("down"))
        return Success(None)


class Service:
    """A ``PreferencesService`` over the real settings classes and a memory store."""

    def __init__(
        self,
        tmp_path: Path,
        saved: Mapping[str, str] | None = None,
        *,
        file_problem: str = "",
    ) -> None:
        self.store = MemoryStore(saved)
        self.events = RecordingEvents(self.store)
        self.applied: list[Settings] = []
        self.fail_apply = False
        self.settings = Settings.load(file_values=saved)
        self.tmp_path = tmp_path
        self.preparer = MeetingsFolderPreparer()
        self.service = PreferencesService(
            self.store,
            PreferencesState(self.settings, dict(saved or {}), file_problem),
            Settings.with_overrides,
            self._apply,
            checks={"storage.meetings_dir": _folder_check},
            prepare={"storage.meetings_dir": lambda raw: self.preparer(raw).map(str)},
            rollback={"storage.meetings_dir": self.preparer.rollback},
            events=self.events,
        )

    def _apply(self, effective: EffectiveSettings) -> None:
        assert isinstance(effective, Settings)
        if self.fail_apply:
            raise RuntimeError("boom private-value")
        self.applied.append(effective)


def _folder_check(raw: str) -> Result[str, FieldProblem]:
    return check_meetings_folder(raw).map(str)
