"""``dev.meetingscribe.Meetings1``: thin D-Bus adapter over ``ProcessingController``."""

import asyncio
import logging
from typing import Annotated

from dbus_fast import PropertyAccess, Variant
from dbus_fast.annotations import DBusSignature, DBusStr, DBusUInt32
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure

from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingController,
    ProcessingStatus,
)
from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.ports import NullProcessingEvents

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Meetings1"

type DBusRow = tuple[str, str, float, str, int]
DBusRows = Annotated[list[DBusRow], DBusSignature("a(ssdsi)")]
DBusDetails = Annotated[dict[str, Variant], DBusSignature("a{sv}")]
DBusStatePair = Annotated[tuple[str, str], DBusSignature("ss")]
DBusFinished = Annotated[tuple[str, str, int], DBusSignature("ssi")]


def _duration(meeting: Meeting) -> float:
    """The audio length once measured, else the recorded time, else zero."""
    if meeting.processing.audio is not None:
        return meeting.processing.audio.duration_seconds
    if meeting.recording.stopped_at is not None:
        return max(
            (meeting.recording.stopped_at - meeting.recording.started_at).total_seconds(), 0.0
        )
    return 0.0


def summary_row(meeting: Meeting) -> DBusRow:
    return (
        meeting.id,
        meeting.recording.started_at.isoformat(),
        _duration(meeting),
        meeting.processing.state,
        len(meeting.speakers),
    )


def details(meeting: Meeting) -> dict[str, Variant]:
    failure = meeting.processing.failure
    transcript = meeting.files.get("transcript", "")
    return {
        "id": Variant("s", meeting.id),
        "started_at": Variant("s", meeting.recording.started_at.isoformat()),
        "duration_seconds": Variant("d", _duration(meeting)),
        "directory": Variant("s", str(meeting.directory)),
        "processing_state": Variant("s", meeting.processing.state),
        "failure_code": Variant("s", failure.error_code if failure else ""),
        "failure_reason": Variant("s", failure.message if failure else ""),
        "speaker_count": Variant("i", len(meeting.speakers)),
        "transcript_path": Variant("s", str(meeting.directory / transcript) if transcript else ""),
    }


class MeetingsInterface(ServiceInterface, NullProcessingEvents):
    """Also the controller's observer: its events (processing thread) become signals on the loop.

    Construct it inside the running event loop. Signals and ``PropertiesChanged`` are emitted
    from the loop thread only; the properties read the controller's thread-safe ``status()``.
    """

    def __init__(self, processing: ProcessingController) -> None:
        ServiceInterface.__init__(self, INTERFACE_NAME)
        self._processing = processing
        self._loop = asyncio.get_running_loop()
        self._last_status = processing.status()

    # ``dbus_method`` wrappers discard the return value when called from Python (GO-022), so the
    # behavior lives in plain coroutines.
    @dbus_method()
    async def List(self) -> DBusRows:
        return await self.list_meetings()

    @dbus_method()
    async def Get(self, meeting_id: DBusStr) -> DBusDetails:
        return await self.get(meeting_id)

    @dbus_method()
    async def Reprocess(self, meeting_id: DBusStr) -> DBusStr:
        return await self.reprocess(meeting_id)

    async def list_meetings(self) -> list[DBusRow]:
        listed = await asyncio.to_thread(self._processing.list_meetings)
        if isinstance(listed, Failure):
            raise to_dbus_error(listed.failure())
        return [summary_row(meeting) for meeting in listed.unwrap()]

    async def get(self, meeting_id: str) -> dict[str, Variant]:
        found = await asyncio.to_thread(self._processing.get_meeting, meeting_id)
        if isinstance(found, Failure):
            raise to_dbus_error(found.failure())
        return details(found.unwrap())

    async def reprocess(self, meeting_id: str) -> str:
        queued = await asyncio.to_thread(self._processing.reprocess, meeting_id)
        if isinstance(queued, Failure):
            raise to_dbus_error(queued.failure())
        return queued.unwrap().processing.state

    @dbus_property(PropertyAccess.READ)
    def ActiveMeetingId(self) -> DBusStr:
        return self._processing.status().active_meeting_id

    @dbus_property(PropertyAccess.READ)
    def ActiveState(self) -> DBusStr:
        return self._processing.status().active_state

    @dbus_property(PropertyAccess.READ)
    def QueuedCount(self) -> DBusUInt32:
        return self._processing.status().queued_count

    @dbus_signal()
    def ProcessingStateChanged(self, meeting_id: DBusStr, state: DBusStr) -> DBusStatePair:
        return (meeting_id, state)

    @dbus_signal()
    def ProcessingFinished(
        self, meeting_id: DBusStr, state: DBusStr, speaker_count: Annotated[int, DBusSignature("i")]
    ) -> DBusFinished:
        return (meeting_id, state, speaker_count)

    # -- ProcessingEvents (called on the processing thread) -------------------------------

    def state_changed(self, meeting: Meeting) -> None:
        self._on_loop(self._emit_state, meeting.id, meeting.processing.state)

    def finished(self, meeting: Meeting) -> None:
        failed = meeting.processing.state != "DONE"
        count = 0 if failed else len(meeting.speakers)
        self._on_loop(self._emit_finished, meeting.id, meeting.processing.state, count)

    def status_changed(self) -> None:
        self._on_loop(self._publish_status)

    # -- loop thread ----------------------------------------------------------------------

    def _on_loop(self, call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            self._loop.call_soon_threadsafe(self._guarded, call, *args)
        except RuntimeError:  # the loop is closed: the service is going away
            logger.debug("Dropped a processing event: the event loop is closed.")

    @staticmethod
    def _guarded(call, *args) -> None:  # noqa: ANN001, ANN002
        try:
            call(*args)
        except Exception:  # e.g. the bus is gone: the state is still on disk
            logger.exception("Could not announce a processing event on the bus.")

    def _emit_state(self, meeting_id: str, state: str) -> None:
        self.ProcessingStateChanged(meeting_id, state)

    def _emit_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:
        self.ProcessingFinished(meeting_id, state, speaker_count)

    def _publish_status(self) -> None:
        status: ProcessingStatus = self._processing.status()  # the freshest, never a stale one
        if status == self._last_status:
            return
        self._last_status = status
        self.emit_properties_changed(
            {
                "ActiveMeetingId": status.active_meeting_id,
                "ActiveState": status.active_state,
                "QueuedCount": status.queued_count,
            }
        )
