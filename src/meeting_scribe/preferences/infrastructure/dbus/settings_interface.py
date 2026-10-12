"""``dev.meetingscribe.Settings1``: thin D-Bus adapter over the preferences use cases (spec 4).

Lives in ``preferences/infrastructure/dbus``. Messages carry keys and codes, never values (a value
can hold a home path), and nothing here knows about the cloud key (BR-005).
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Annotated

from dbus_fast import PropertyAccess, Variant
from dbus_fast.annotations import DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.preferences.application.restart_service import RestartService
from meeting_scribe.preferences.application.settings_service import (
    PreferencesService,
    SetOutcome,
)
from meeting_scribe.preferences.domain.fields import FieldRow
from meeting_scribe.preferences.domain.models import MODELS_UNAVAILABLE, ModelListing
from meeting_scribe.preferences.domain.ports import ModelCatalog, NullSettingsEvents

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Settings1"
RESTART_DELAY_SECONDS = 0.3
"""Between the reply and the restart request, so the reply is on the wire first."""

DBusDetails = Annotated[dict[str, Variant], DBusSignature("a{sv}")]
DBusRows = Annotated[list[dict[str, Variant]], DBusSignature("aa{sv}")]
DBusValues = Annotated[dict[str, str], DBusSignature("a{ss}")]
DBusKeys = Annotated[list[str], DBusSignature("as")]


def field_row(row: FieldRow) -> dict[str, Variant]:
    return {
        "key": Variant("s", row.key),
        "kind": Variant("s", row.kind),
        "value": Variant("s", row.value),
        "default": Variant("s", row.default),
        "source": Variant("s", row.source),
        "locked": Variant("b", row.locked),
        "lock_reason": Variant("s", row.lock_reason),
        "apply": Variant("s", row.apply),
        "choices": Variant("as", list(row.choices)),
        "file_problem": Variant("s", row.file_problem),
        "apply_problem": Variant("s", row.apply_problem),
    }


def model_listing(listing: ModelListing) -> dict[str, Variant]:
    return {
        "status": Variant("s", listing.status),
        "models": Variant("a(sbs)", [[m.id, m.supported, m.reason] for m in listing.models]),
    }


def outcome_details(outcome: SetOutcome) -> dict[str, Variant]:
    return {
        "applied": Variant("as", list(outcome.applied)),
        "restart_required": Variant("as", list(outcome.restart_required)),
        "apply_failed": Variant("b", outcome.apply_failed),
        "errors": Variant("a(sss)", [[e.key, str(e.code), e.message] for e in outcome.errors]),
    }


class _NoModels:
    def list_models(self, scope: str) -> ModelListing:
        del scope
        return ModelListing(MODELS_UNAVAILABLE)

    def cached_models(self, scope: str) -> ModelListing | None:
        del scope
        return None


class SettingsInterface(ServiceInterface, NullSettingsEvents):
    """Also the service's observer: saves (on a worker thread) become signals on the loop.

    Construct it inside the running event loop; signals and ``PropertiesChanged`` are emitted from
    the loop thread only.
    """

    def __init__(
        self,
        preferences: PreferencesService,
        restart: RestartService,
        restart_delay: float = RESTART_DELAY_SECONDS,
        models: ModelCatalog | None = None,
    ) -> None:
        ServiceInterface.__init__(self, INTERFACE_NAME)
        self._preferences = preferences
        self._restart = restart
        self._restart_delay = restart_delay
        self._models = models or _NoModels()
        self._loop = asyncio.get_running_loop()
        self._tasks: set[asyncio.Task[None]] = set()

    # ``dbus_method`` wrappers discard the return value when called from Python (GO-022), so the
    # behavior lives in plain coroutines.
    @dbus_method()
    async def GetAll(self) -> DBusRows:
        return await self.get_all()

    @dbus_method()
    async def Set(self, values: DBusValues) -> DBusDetails:
        return await self.set_values(values)

    @dbus_method()
    async def Restart(self) -> DBusStr:
        return await self.restart()

    @dbus_method()
    async def ListModels(self, scope: DBusStr) -> DBusDetails:
        return await self.list_models(scope)

    async def get_all(self) -> list[dict[str, Variant]]:
        rows = await asyncio.to_thread(self._preferences.get_all)
        return [field_row(row) for row in rows]

    async def set_values(self, values: dict[str, str]) -> dict[str, Variant]:
        saved = await asyncio.to_thread(self._preferences.set, values)
        if isinstance(saved, Failure):
            raise to_dbus_error(saved.failure())
        return outcome_details(saved.unwrap())

    async def list_models(self, scope: str) -> dict[str, Variant]:
        """Bounded network calls, off the loop. Never a D-Bus error: the status says why."""
        listing = await asyncio.to_thread(self._models.list_models, scope)
        return model_listing(listing)

    async def restart(self) -> str:
        checked = await self._restart.check()
        if isinstance(checked, Failure):
            raise to_dbus_error(checked.failure())
        self._loop.call_later(self._restart_delay, self._begin_restart)
        return checked.unwrap()

    @dbus_property(PropertyAccess.READ)
    def RestartRequired(self) -> DBusKeys:
        return list(self._preferences.restart_required)

    @dbus_signal()
    def SettingsChanged(self, keys: DBusKeys) -> DBusKeys:
        return keys

    # -- SettingsEvents (called on the thread that saved) ----------------------------------

    def changed(self, keys: Sequence[str]) -> None:
        self._on_loop(self.SettingsChanged, list(keys))

    def restart_required_changed(self, keys: Sequence[str]) -> None:
        self._on_loop(self.emit_properties_changed, {"RestartRequired": list(keys)})

    # -- loop thread ----------------------------------------------------------------------

    def _on_loop(self, call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            self._loop.call_soon_threadsafe(self._guarded, call, *args)
        except RuntimeError:  # the loop is closed: the service is going away
            logger.debug("Dropped a settings event: the event loop is closed.")

    @staticmethod
    def _guarded(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:  # e.g. the bus is gone: the file is already saved
            logger.exception("Could not announce a settings event on the bus.")

    def _begin_restart(self) -> None:
        task = self._loop.create_task(self._restart.perform())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
