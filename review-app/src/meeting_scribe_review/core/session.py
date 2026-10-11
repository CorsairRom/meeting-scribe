"""One review window's presenters, wired together without any widget (AC-13).

The views only implement ``show_*``; which meeting is open, and what each service event refreshes,
is decided here so it is tested without a display.
"""

from typing import Protocol

from meeting_scribe_review.core.gateway import MeetingsGateway
from meeting_scribe_review.core.presenters.meeting_list import (
    MeetingListPresenter,
    MeetingListState,
    MeetingListStatus,
)
from meeting_scribe_review.core.presenters.review import ReviewPresenter, ReviewState, ReviewStatus


class SessionViews(Protocol):
    def show_meetings(self, state: MeetingListState) -> None: ...

    def show_review(self, state: ReviewState) -> None: ...


class ReviewSession:
    """Also the gateway listener: it hands each service event to the presenters that need it."""

    def __init__(self, gateway: MeetingsGateway, views: SessionViews) -> None:
        self._views = views
        self._meetings = MeetingListPresenter(gateway, self._meetings_changed)
        self._review = ReviewPresenter(gateway, views.show_review)
        self._open_id = ""
        gateway.watch(self)

    @property
    def meetings(self) -> MeetingListState:
        return self._meetings.state

    @property
    def review(self) -> ReviewState:
        return self._review.state

    def start(self, preferred_id: str = "") -> None:
        """Load the list; open ``preferred_id``, else the current meeting, else the newest DONE."""
        self._meetings.load(preferred_id)

    def select(self, meeting_id: str) -> None:
        self._meetings.select(meeting_id)

    def retry(self) -> None:
        self._meetings.load()
        if self._review.state.status is ReviewStatus.ERROR:
            self._review.reload()

    def close(self) -> None:
        self._open_id = ""
        self._review.close()

    def on_service_available(self, available: bool) -> None:
        self._meetings.on_service_available(available)
        self._review.on_service_available(available)

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:
        self._meetings.on_processing_state_changed(meeting_id, state)
        self._review.on_processing_state_changed(meeting_id, state)

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:
        self._meetings.on_processing_finished(meeting_id, state, speaker_count)
        self._review.on_processing_finished(meeting_id, state, speaker_count)

    def on_speakers_changed(self, meeting_id: str) -> None:
        self._meetings.on_speakers_changed(meeting_id)
        self._review.on_speakers_changed(meeting_id)

    def _meetings_changed(self, state: MeetingListState) -> None:
        self._views.show_meetings(state)
        if state.status is MeetingListStatus.ERROR:
            return  # keep the open review; its own error comes from the review presenter
        if state.selected_id == self._open_id:
            return
        self._open_id = state.selected_id
        if not state.selected_id:
            self._review.close()
            return
        row = next(row for row in state.rows if row.id == state.selected_id)
        self._review.open(row.id, row.state)
