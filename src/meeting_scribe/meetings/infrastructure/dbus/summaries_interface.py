"""``dev.meetingscribe.Summaries1``: thin D-Bus adapter over ``SummaryController`` (spec 10).

Lives in ``meetings`` (it needs ``meeting.json``), not in ``summaries`` (domains do not cross).
Errors carry codes and fixed messages only: never a transcript line, a name, a summary text or
the key.
"""

import asyncio
import logging
from typing import Annotated

from dbus_fast import PropertyAccess, Variant
from dbus_fast.annotations import DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.meetings.application.summary_controller import (
    LastSummary,
    ProviderRow,
    SummaryController,
    SummaryDocument,
    SummaryStatus,
)
from meeting_scribe.meetings.application.summary_events import ProgressThrottle
from meeting_scribe.meetings.domain.ports import NullSummaryEvents, SummaryOutcome

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Summaries1"
PROGRESS_INTERVAL_SECONDS = 1.0

DBusDetails = Annotated[dict[str, Variant], DBusSignature("a{sv}")]
DBusProviderRows = Annotated[list[dict[str, Variant]], DBusSignature("aa{sv}")]
DBusBool = Annotated[bool, DBusSignature("b")]
DBusInt = Annotated[int, DBusSignature("i")]
DBusProgress = Annotated[tuple[str, int, int], DBusSignature("sii")]
DBusFinished = Annotated[tuple[str, str, str], DBusSignature("sss")]


def _iso(value) -> str:  # noqa: ANN001 - a datetime or None
    return value.isoformat() if value is not None else ""


def provider_row(row: ProviderRow) -> dict[str, Variant]:
    return {
        "id": Variant("s", row.id),
        "label": Variant("s", row.label),
        "cloud": Variant("b", row.cloud),
        "model": Variant("s", row.model),
        "available": Variant("b", row.available),
        "reason": Variant("s", row.reason),
        "default": Variant("b", row.default),
    }


def status_details(status: SummaryStatus) -> dict[str, Variant]:
    return {
        "state": Variant("s", status.state),
        "stale": Variant("b", status.stale),
        "cloud": Variant("b", status.cloud),
        "provider_label": Variant("s", status.provider_label),
        "generated_at": Variant("s", _iso(status.generated_at)),
        "step": Variant("i", status.step),
        "total_steps": Variant("i", status.total_steps),
        "failure_code": Variant("s", status.failure_code),
    }


def document_details(document: SummaryDocument) -> dict[str, Variant]:
    return {
        "markdown": Variant("s", document.markdown),
        "title": Variant("s", document.title),
        "generated_at": Variant("s", _iso(document.generated_at)),
        "provider_label": Variant("s", document.provider_label),
        "cloud": Variant("b", document.cloud),
        "model": Variant("s", document.model),
        "language": Variant("s", document.language),
        "stale": Variant("b", document.stale),
    }


def last_details(last: LastSummary) -> dict[str, Variant]:
    return {
        "meeting_id": Variant("s", last.meeting_id),
        "started_at": Variant("s", last.started_at.isoformat()),
        "generated_at": Variant("s", last.generated_at.isoformat()),
        "path": Variant("s", str(last.path)),
    }


class SummariesInterface(ServiceInterface, NullSummaryEvents):
    """Also the controller's observer: its events (job thread) become signals on the loop.

    Construct it inside the running event loop. Signals and ``PropertiesChanged`` are emitted
    from the loop thread only; the property reads the controller's thread-safe
    ``active_meeting_id``.
    """

    def __init__(
        self, summaries: SummaryController, throttle: ProgressThrottle | None = None
    ) -> None:
        ServiceInterface.__init__(self, INTERFACE_NAME)
        self._summaries = summaries
        self._throttle = throttle or ProgressThrottle(PROGRESS_INTERVAL_SECONDS)
        self._loop = asyncio.get_running_loop()
        self._last_active = summaries.active_meeting_id

    # ``dbus_method`` wrappers discard the return value when called from Python (GO-022), so the
    # behavior lives in plain coroutines.
    @dbus_method()
    async def Providers(self) -> DBusProviderRows:
        return await self.providers()

    @dbus_method()
    async def Summarize(
        self, meeting_id: DBusStr, provider_id: DBusStr, cloud_confirmed: DBusBool
    ) -> DBusStr:
        return await self.summarize(meeting_id, provider_id, cloud_confirmed)

    @dbus_method()
    async def CancelSummary(self, meeting_id: DBusStr) -> DBusBool:
        return await self.cancel_summary(meeting_id)

    @dbus_method()
    async def GetSummaryStatus(self, meeting_id: DBusStr) -> DBusDetails:
        return await self.get_summary_status(meeting_id)

    @dbus_method()
    async def GetSummary(self, meeting_id: DBusStr) -> DBusDetails:
        return await self.get_summary(meeting_id)

    @dbus_method()
    async def LastSummary(self) -> DBusDetails:
        return await self.last_summary()

    async def providers(self) -> list[dict[str, Variant]]:
        return [provider_row(row) for row in self._summaries.providers()]

    async def summarize(self, meeting_id: str, provider_id: str, cloud_confirmed: bool) -> str:
        started = await asyncio.to_thread(
            self._summaries.summarize, meeting_id, provider_id, cloud_confirmed
        )
        if isinstance(started, Failure):
            raise to_dbus_error(started.failure())
        return started.unwrap()

    async def cancel_summary(self, meeting_id: str) -> bool:
        cancelled = await asyncio.to_thread(self._summaries.cancel, meeting_id)
        if isinstance(cancelled, Failure):
            raise to_dbus_error(cancelled.failure())
        return cancelled.unwrap()

    async def get_summary_status(self, meeting_id: str) -> dict[str, Variant]:
        status = await asyncio.to_thread(self._summaries.get_status, meeting_id)
        if isinstance(status, Failure):
            raise to_dbus_error(status.failure())
        return status_details(status.unwrap())

    async def get_summary(self, meeting_id: str) -> dict[str, Variant]:
        document = await asyncio.to_thread(self._summaries.get_summary, meeting_id)
        if isinstance(document, Failure):
            raise to_dbus_error(document.failure())
        return document_details(document.unwrap())

    async def last_summary(self) -> dict[str, Variant]:
        last = await asyncio.to_thread(self._summaries.last_summary)
        if isinstance(last, Failure):
            raise to_dbus_error(last.failure())
        return last_details(last.unwrap())

    @dbus_property(PropertyAccess.READ)
    def ActiveSummaryMeetingId(self) -> DBusStr:
        return self._summaries.active_meeting_id

    @dbus_signal()
    def SummaryProgress(
        self, meeting_id: DBusStr, step: DBusInt, total_steps: DBusInt
    ) -> DBusProgress:
        return (meeting_id, step, total_steps)

    @dbus_signal()
    def SummaryFinished(
        self, meeting_id: DBusStr, state: DBusStr, error_code: DBusStr
    ) -> DBusFinished:
        return (meeting_id, state, error_code)

    # -- SummaryEvents (called on the summary job thread) ---------------------------------

    def progress(self, meeting_id: str, step: int, total: int) -> None:
        if self._throttle.allow(step, total):
            self._on_loop(self._emit_progress, meeting_id, step, total)

    def finished(self, meeting_id: str, state: SummaryOutcome, error_code: str) -> None:
        self._throttle.reset()
        self._on_loop(self._emit_finished, meeting_id, state, error_code)

    def active_changed(self) -> None:
        self._throttle.reset()
        self._on_loop(self._publish_active)

    # -- loop thread ----------------------------------------------------------------------

    def _on_loop(self, call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            self._loop.call_soon_threadsafe(self._guarded, call, *args)
        except RuntimeError:  # the loop is closed: the service is going away
            logger.debug("Dropped a summary event: the event loop is closed.")

    @staticmethod
    def _guarded(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:  # e.g. the bus is gone: the result is still on disk
            logger.exception("Could not announce a summary event on the bus.")

    def _emit_progress(self, meeting_id: str, step: int, total: int) -> None:
        self.SummaryProgress(meeting_id, step, total)

    def _emit_finished(self, meeting_id: str, state: str, error_code: str) -> None:
        self.SummaryFinished(meeting_id, state, error_code)

    def _publish_active(self) -> None:
        active = self._summaries.active_meeting_id  # the freshest, never a stale one
        if active == self._last_active:
            return
        self._last_active = active
        self.emit_properties_changed({"ActiveSummaryMeetingId": active})
