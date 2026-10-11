"""``dev.meetingscribe.Meetings1``: thin D-Bus adapter over ``ProcessingController``."""

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated

from dbus_fast import PropertyAccess, Variant
from dbus_fast.annotations import DBusSignature, DBusStr, DBusUInt32
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal
from returns.result import Failure, Result

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingController,
    ProcessingStatus,
)
from meeting_scribe.meetings.application.review_controller import ReviewController, ReviewResult
from meeting_scribe.meetings.domain.meeting import Meeting
from meeting_scribe.meetings.domain.ports import NullProcessingEvents
from meeting_scribe.speakers.application.review_speakers import EffectiveSpeaker

logger = logging.getLogger(__name__)

INTERFACE_NAME = "dev.meetingscribe.Meetings1"

type DBusRow = tuple[str, str, float, str, int]
DBusRows = Annotated[list[DBusRow], DBusSignature("a(ssdsi)")]
DBusDetails = Annotated[dict[str, Variant], DBusSignature("a{sv}")]
DBusStatePair = Annotated[tuple[str, str], DBusSignature("ss")]
DBusFinished = Annotated[tuple[str, str, int], DBusSignature("ssi")]
DBusSpeakerRows = Annotated[list[dict[str, Variant]], DBusSignature("aa{sv}")]
DBusUtteranceRows = Annotated[list[tuple[str, float, float, str]], DBusSignature("a(sdds)")]
DBusBytes = Annotated[bytes, DBusSignature("ay")]


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
        meeting.active_speaker_count,
    )


def speaker_row(speaker: EffectiveSpeaker) -> dict[str, Variant]:
    return {
        "id": Variant("s", speaker.id),
        "label": Variant("s", speaker.label),
        "name": Variant("s", speaker.name),
        "display": Variant("s", speaker.display),
        "is_me": Variant("b", speaker.is_me),
        "talk_seconds": Variant("d", speaker.talk_seconds),
        "utterance_count": Variant("i", speaker.utterance_count),
        "has_sample": Variant("b", speaker.has_sample),
        "merged_into": Variant("s", speaker.merged_into),
    }


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
        "speaker_count": Variant("i", meeting.active_speaker_count),
        "transcript_path": Variant("s", str(meeting.directory / transcript) if transcript else ""),
    }


class MeetingsInterface(ServiceInterface, NullProcessingEvents):
    """Also the controller's observer: its events (processing thread) become signals on the loop.

    Construct it inside the running event loop. Signals and ``PropertiesChanged`` are emitted
    from the loop thread only; the properties read the controller's thread-safe ``status()``.
    """

    def __init__(self, processing: ProcessingController, review: ReviewController) -> None:
        ServiceInterface.__init__(self, INTERFACE_NAME)
        self._processing = processing
        self._review = review
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

    @dbus_method()
    async def GetSpeakers(self, meeting_id: DBusStr) -> DBusSpeakerRows:
        return await self.get_speakers(meeting_id)

    @dbus_method()
    async def GetTranscript(self, meeting_id: DBusStr) -> DBusUtteranceRows:
        return await self.get_transcript(meeting_id)

    @dbus_method()
    async def GetSpeakerSample(self, meeting_id: DBusStr, speaker_id: DBusStr) -> DBusBytes:
        return await self.get_speaker_sample(meeting_id, speaker_id)

    @dbus_method()
    async def RenameSpeaker(
        self, meeting_id: DBusStr, speaker_id: DBusStr, name: DBusStr
    ) -> DBusSpeakerRows:
        return await self.rename_speaker(meeting_id, speaker_id, name)

    @dbus_method()
    async def MergeSpeakers(
        self, meeting_id: DBusStr, source_id: DBusStr, target_id: DBusStr
    ) -> DBusSpeakerRows:
        return await self.merge_speakers(meeting_id, source_id, target_id)

    @dbus_method()
    async def UnmergeSpeaker(self, meeting_id: DBusStr, speaker_id: DBusStr) -> DBusSpeakerRows:
        return await self.unmerge_speaker(meeting_id, speaker_id)

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

    async def get_speakers(self, meeting_id: str) -> list[dict[str, Variant]]:
        found = await asyncio.to_thread(self._review.speakers, meeting_id)
        if isinstance(found, Failure):
            raise to_dbus_error(found.failure())
        return [speaker_row(speaker) for speaker in found.unwrap()]

    async def get_transcript(self, meeting_id: str) -> list[tuple[str, float, float, str]]:
        found = await asyncio.to_thread(self._review.transcript, meeting_id)
        if isinstance(found, Failure):
            raise to_dbus_error(found.failure())
        return [(u.speaker, u.start, u.end, u.text) for u in found.unwrap()]

    async def get_speaker_sample(self, meeting_id: str, speaker_id: str) -> bytes:
        found = await asyncio.to_thread(self._review.speaker_sample, meeting_id, speaker_id)
        if isinstance(found, Failure):
            raise to_dbus_error(found.failure())
        return found.unwrap()

    async def rename_speaker(
        self, meeting_id: str, speaker_id: str, name: str
    ) -> list[dict[str, Variant]]:
        return await self._edited(meeting_id, self._review.rename, speaker_id, name)

    async def merge_speakers(
        self, meeting_id: str, source_id: str, target_id: str
    ) -> list[dict[str, Variant]]:
        return await self._edited(meeting_id, self._review.merge, source_id, target_id)

    async def unmerge_speaker(self, meeting_id: str, speaker_id: str) -> list[dict[str, Variant]]:
        return await self._edited(meeting_id, self._review.unmerge, speaker_id)

    async def _edited(
        self,
        meeting_id: str,
        edit: Callable[..., Result[ReviewResult, MeetingScribeError]],
        *args: str,
    ) -> list[dict[str, Variant]]:
        """Run one review edit off the loop; announce it only when something changed."""
        outcome = await asyncio.to_thread(edit, meeting_id, *args)
        if isinstance(outcome, Failure):
            raise to_dbus_error(outcome.failure())
        result = outcome.unwrap()
        if result.changed:
            self._emit_speakers_changed(meeting_id)
        return [speaker_row(speaker) for speaker in result.speakers]

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

    @dbus_signal()
    def SpeakersChanged(self, meeting_id: DBusStr) -> DBusStr:
        return meeting_id

    # -- ProcessingEvents (called on the processing thread) -------------------------------

    def state_changed(self, meeting: Meeting) -> None:
        self._on_loop(self._emit_state, meeting.id, meeting.processing.state)

    def finished(self, meeting: Meeting) -> None:
        failed = meeting.processing.state != "DONE"
        count = 0 if failed else len(meeting.speakers)
        self._on_loop(self._emit_finished, meeting.id, meeting.processing.state, count)
        if not failed:  # a reprocess clears the review: the speakers are new
            self._on_loop(self._emit_speakers_changed, meeting.id)

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

    def _emit_speakers_changed(self, meeting_id: str) -> None:
        self.SpeakersChanged(meeting_id)

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
