"""Read and change the exposed settings (spec ``preferences.md`` sections 3 to 6).

The validators are not here: the composition root injects ``Settings.with_overrides`` as
``validate`` (one rule set, AC-3) and a per-field check for the meetings folder. A ``Set`` is one
transaction under a lock: check every field, validate the merged candidate, write the file, then
apply and signal. Logs carry keys and codes only (AC-14).
"""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.errors import SettingsStorageFailedError
from meeting_scribe.preferences.domain.fields import (
    FIELD_REGISTRY,
    FieldCode,
    FieldProblem,
    FieldRow,
    field_spec,
)
from meeting_scribe.preferences.domain.ports import (
    EffectiveSettings,
    NullSettingsEvents,
    SettingsEvents,
    SettingsFileStore,
)

logger = logging.getLogger(__name__)

type Validator = Callable[[Mapping[str, str]], Result[EffectiveSettings, list[FieldProblem]]]
type FieldCheck = Callable[[str], Result[str, FieldProblem]]
"""Checks one raw value of one field against the world (a folder) and returns what to store."""


@dataclass(frozen=True)
class PreferencesState:
    """What the service started with: the settings in effect and the saved values behind them."""

    effective: EffectiveSettings
    saved: Mapping[str, str] = field(default_factory=dict)
    file_problem: str = ""


@dataclass(frozen=True)
class SetOutcome:
    applied: tuple[str, ...]
    restart_required: tuple[str, ...]
    errors: tuple[FieldProblem, ...]


class PreferencesService:
    def __init__(
        self,
        store: SettingsFileStore,
        state: PreferencesState,
        validate: Validator,
        apply: Callable[[EffectiveSettings], None],
        *,
        checks: Mapping[str, FieldCheck] | None = None,
        events: SettingsEvents | None = None,
    ) -> None:
        self._store = store
        self._validate = validate
        self._apply = apply
        self._checks = dict(checks or {})
        self._events: SettingsEvents = events or NullSettingsEvents()
        self._lock = threading.Lock()  # one Set at a time; the second waits
        self._effective = state.effective
        self._saved = dict(state.saved)
        self._file_problem = state.file_problem
        # The restart fields as the running controllers captured them at start.
        self._running = {
            spec.key: state.effective.exposed_value(spec.key)
            for spec in FIELD_REGISTRY
            if spec.apply == "restart"
        }
        self._pending: tuple[str, ...] = ()

    def subscribe(self, events: SettingsEvents) -> None:
        self._events = events

    @property
    def restart_required(self) -> tuple[str, ...]:
        return self._pending

    def get_all(self) -> list[FieldRow]:
        effective = self._effective
        rows: list[FieldRow] = []
        for spec in FIELD_REGISTRY:
            source = effective.source_of(spec.key)
            rows.append(
                FieldRow(
                    key=spec.key,
                    kind=spec.kind,
                    value=effective.exposed_value(spec.key),
                    default=effective.exposed_default(spec.key),
                    source=source.source,
                    locked=source.locked,
                    lock_reason=source.lock_reason,
                    apply=spec.apply,
                    choices=spec.choices,
                    file_problem=self._file_problem,
                )
            )
        return rows

    def set(self, values: Mapping[str, str]) -> Result[SetOutcome, SettingsStorageFailedError]:
        with self._lock:
            return self._set(dict(values))

    # -- the transaction (lock held) --

    def _set(self, values: dict[str, str]) -> Result[SetOutcome, SettingsStorageFailedError]:
        problems, candidate = self._candidate(values)
        if not problems:
            validated = self._validate(candidate)
            if isinstance(validated, Failure):
                problems = list(validated.failure())
            else:
                return self._save(values, candidate, validated.unwrap())
        keys = [problem.key for problem in problems]
        logger.warning("Settings rejected: %d problem(s) on keys %s", len(problems), keys)
        return Success(SetOutcome((), self._pending, tuple(problems)))

    def _candidate(self, values: dict[str, str]) -> tuple[list[FieldProblem], dict[str, str]]:
        """The merged saved values, plus a problem per unknown, locked or unusable field."""
        problems: list[FieldProblem] = []
        candidate = dict(self._saved)
        for key, raw in values.items():
            if field_spec(key) is None:
                problems.append(
                    FieldProblem(key, FieldCode.UNKNOWN, "This setting cannot be changed here.")
                )
                continue
            if self._effective.source_of(key).locked:
                problems.append(
                    FieldProblem(
                        key,
                        FieldCode.LOCKED,
                        "The environment sets this field; remove that override to change it here.",
                    )
                )
                continue
            check = self._checks.get(key)
            checked = check(raw) if check is not None else Success(raw)
            if isinstance(checked, Failure):
                problems.append(checked.failure())
                continue
            self._merge(candidate, key, checked.unwrap())
        return problems, candidate

    def _merge(self, candidate: dict[str, str], key: str, value: str) -> None:
        """Sparse file: a value equal to the default is removed, so new defaults still reach you."""
        if value == self._effective.exposed_default(key):
            candidate.pop(key, None)
        else:
            candidate[key] = value

    def _save(
        self, values: dict[str, str], candidate: dict[str, str], effective: EffectiveSettings
    ) -> Result[SetOutcome, SettingsStorageFailedError]:
        written = self._store.write(candidate)
        if isinstance(written, Failure):
            return written
        self._saved = candidate
        self._effective = effective
        self._file_problem = ""
        keys = tuple(values)
        logger.info("Settings saved: keys %s", list(keys))
        self._apply(effective)
        pending = tuple(
            key for key, running in self._running.items() if effective.exposed_value(key) != running
        )
        pending_changed = pending != self._pending
        self._pending = pending
        self._events.changed(keys)
        if pending_changed:
            self._events.restart_required_changed(pending)
        return Success(SetOutcome(keys, pending, ()))
