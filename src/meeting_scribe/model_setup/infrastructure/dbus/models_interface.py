"""``dev.meetingscribe.Models1``: thin D-Bus adapter over ``ModelSetupController`` (spec install 7).

Lives in ``model_setup/infrastructure/dbus``. Messages carry codes and states only: never a token,
a path or the download process's output, and no member takes or returns a token (it is read from
the keyring by the service and written to the child's stdin).
"""

import asyncio
import logging
from typing import Annotated

from dbus_fast import PropertyAccess, Variant
from dbus_fast.annotations import DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.model_setup.application.model_setup_controller import ModelSetupController
from meeting_scribe.model_setup.domain.models import ModelsStatus, Outcome
from meeting_scribe.model_setup.domain.ports import NullDownloadEvents

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Models1"

DBusDetails = Annotated[dict[str, Variant], DBusSignature("a{sv}")]
DBusBool = Annotated[bool, DBusSignature("b")]
DBusInt = Annotated[int, DBusSignature("i")]
DBusProgress = Annotated[tuple[str, int, int], DBusSignature("sii")]
DBusFinished = Annotated[tuple[str, str], DBusSignature("ss")]


def status_details(status: ModelsStatus) -> dict[str, Variant]:
    return {
        "state": Variant("s", status.state),
        "whisper_cached": Variant("b", status.whisper_cached),
        "pipeline_cached": Variant("b", status.pipeline_cached),
        "token_stored": Variant("b", status.token_stored),
        "step": Variant("i", status.step),
        "total_steps": Variant("i", status.total_steps),
        "failure_code": Variant("s", status.failure_code),
    }


class ModelsInterface(ServiceInterface, NullDownloadEvents):
    """Also the controller's observer: its events (download thread) become signals on the loop.

    Construct it inside the running event loop. Signals and ``PropertiesChanged`` are emitted from
    the loop thread only; the property reads the controller's thread-safe ``active``. A download
    emits at most two ``ModelsProgress`` (one per step), so the one-per-second bound holds without
    a throttle.
    """

    def __init__(self, models: ModelSetupController) -> None:
        ServiceInterface.__init__(self, INTERFACE_NAME)
        self._models = models
        self._loop = asyncio.get_running_loop()
        self._last_active = models.active

    # ``dbus_method`` wrappers discard the return value when called from Python (GO-022), so the
    # behavior lives in plain coroutines.
    @dbus_method()
    async def GetStatus(self) -> DBusDetails:
        return await self.get_status()

    @dbus_method()
    async def Download(self) -> DBusStr:
        return await self.download()

    @dbus_method()
    async def CancelDownload(self) -> DBusBool:
        return await self.cancel_download()

    async def get_status(self) -> dict[str, Variant]:
        """The first call may run the bounded offline cache check: off the loop."""
        return status_details(await asyncio.to_thread(self._models.status))

    async def download(self) -> str:
        """Checks the cache afresh (a bounded child process): off the loop."""
        started = await asyncio.to_thread(self._models.download)
        if isinstance(started, Failure):
            raise to_dbus_error(started.failure())
        return started.unwrap()

    async def cancel_download(self) -> bool:
        return self._models.cancel()

    @dbus_property(PropertyAccess.READ)
    def ActiveDownload(self) -> DBusBool:
        return self._models.active

    @dbus_signal()
    def ModelsProgress(self, model: DBusStr, step: DBusInt, total_steps: DBusInt) -> DBusProgress:
        return (model, step, total_steps)

    @dbus_signal()
    def ModelsFinished(self, state: DBusStr, error_code: DBusStr) -> DBusFinished:
        return (state, error_code)

    # -- DownloadEvents (called on the download thread) ------------------------------------

    def progress(self, model: str, step: int, total: int) -> None:
        self._on_loop(self.ModelsProgress, model, step, total)

    def finished(self, state: Outcome, error_code: str) -> None:
        self._on_loop(self.ModelsFinished, state, error_code)

    def active_changed(self) -> None:
        self._on_loop(self._publish_active)

    # -- loop thread ----------------------------------------------------------------------

    def _on_loop(self, call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            self._loop.call_soon_threadsafe(self._guarded, call, *args)
        except RuntimeError:  # the loop is closed: the service is going away
            logger.debug("Dropped a download event: the event loop is closed.")

    @staticmethod
    def _guarded(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:  # e.g. the bus is gone: the status is still correct
            logger.exception("Could not announce a download event on the bus.")

    def _publish_active(self) -> None:
        active = self._models.active  # the freshest, never a stale one
        if active == self._last_active:
            return
        self._last_active = active
        self.emit_properties_changed({"ActiveDownload": active})
