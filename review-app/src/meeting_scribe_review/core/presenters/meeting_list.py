"""The meetings sidebar: which meeting is selected and what the rows say (AC-13, US-6)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from meeting_scribe_review.core.errors import (
    SERVICE_NOT_RUNNING,
    ServiceError,
    message_for,
)
from meeting_scribe_review.core.formatting import format_duration
from meeting_scribe_review.core.gateway import MeetingsGateway
from meeting_scribe_review.core.models import MeetingSummary
from meeting_scribe_review.core.result import Err, Result
from meeting_scribe_review.core.states import STATE_DONE, state_label

EMPTY_TEXT = "No reviewed meetings yet"


class MeetingListStatus(StrEnum):
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MeetingRow:
    id: str
    started_at: datetime  # the view formats it in the user's locale
    duration_text: str
    state: str
    state_label: str
    speaker_count: int
    reviewable: bool


@dataclass(frozen=True, slots=True)
class MeetingListState:
    status: MeetingListStatus = MeetingListStatus.LOADING
    rows: tuple[MeetingRow, ...] = ()
    selected_id: str = ""
    message: str = ""
    detail: str = ""


def _row(meeting: MeetingSummary) -> MeetingRow:
    return MeetingRow(
        id=meeting.id,
        started_at=meeting.started_at,
        duration_text=format_duration(meeting.duration_seconds),
        state=meeting.state,
        state_label=state_label(meeting.state),
        speaker_count=meeting.active_speaker_count,
        reviewable=meeting.state == STATE_DONE,
    )


class MeetingListPresenter:
    """Loads ``List`` and chooses the selection; the service lists newest first."""

    def __init__(
        self,
        gateway: MeetingsGateway,
        on_change: Callable[[MeetingListState], None],
    ) -> None:
        self._gateway = gateway
        self._on_change = on_change
        self._state = MeetingListState()

    @property
    def state(self) -> MeetingListState:
        return self._state

    def load(self, preferred_id: str = "") -> None:
        """Fetch the list; select ``preferred_id``, else the current one, else the newest DONE."""
        wanted = preferred_id or self._state.selected_id
        self._gateway.list_meetings(lambda result: self._loaded(result, wanted))

    def select(self, meeting_id: str) -> None:
        if any(row.id == meeting_id for row in self._state.rows):
            self._publish(
                MeetingListState(MeetingListStatus.READY, self._state.rows, meeting_id, "", "")
            )

    def on_service_available(self, available: bool) -> None:
        if available:
            self.load()
        else:
            self._fail(ServiceError(SERVICE_NOT_RUNNING))

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:  # noqa: ARG002
        self.load()

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:  # noqa: ARG002
        self.load()

    def on_speakers_changed(self, meeting_id: str) -> None:  # noqa: ARG002
        self.load()

    def _loaded(self, result: Result[list[MeetingSummary], ServiceError], wanted: str) -> None:
        if isinstance(result, Err):
            self._fail(result.error)
            return
        rows = tuple(_row(meeting) for meeting in result.value)
        selected = self._choose(rows, wanted)
        if selected:
            self._publish(MeetingListState(MeetingListStatus.READY, rows, selected))
            return
        detail = f"Latest meeting: {rows[0].state_label}" if rows else ""
        self._publish(MeetingListState(MeetingListStatus.EMPTY, rows, "", EMPTY_TEXT, detail))

    @staticmethod
    def _choose(rows: tuple[MeetingRow, ...], wanted: str) -> str:
        if wanted and any(row.id == wanted for row in rows):
            return wanted
        return next((row.id for row in rows if row.reviewable), "")

    def _fail(self, error: ServiceError) -> None:
        message = message_for(error)
        self._publish(MeetingListState(MeetingListStatus.ERROR, (), "", message.text, message.hint))

    def _publish(self, state: MeetingListState) -> None:
        self._state = state
        self._on_change(state)
