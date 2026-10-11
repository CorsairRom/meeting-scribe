"""One review window's presenters, wired together without any widget (AC-13).

The views only implement ``show_*``; which meeting is open, and what each service event refreshes,
is decided here so it is tested without a display.
"""

from collections.abc import Sequence
from typing import Protocol

from meeting_scribe_review.core.gateway import (
    ApiKeyStore,
    MeetingsGateway,
    SamplePlayer,
    SummariesGateway,
)
from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.presenters.api_key import ApiKeyPresenter, ApiKeyState
from meeting_scribe_review.core.presenters.cloud_confirmation import (
    CloudConfirmationPresenter,
    ConfirmationRequest,
    ConfirmationState,
)
from meeting_scribe_review.core.presenters.meeting_list import (
    MeetingListPresenter,
    MeetingListState,
    MeetingListStatus,
)
from meeting_scribe_review.core.presenters.merge import MergePresenter, MergeState
from meeting_scribe_review.core.presenters.playback import PlaybackPresenter, PlaybackState
from meeting_scribe_review.core.presenters.rename import RenamePresenter, RenameState
from meeting_scribe_review.core.presenters.review import ReviewPresenter, ReviewState, ReviewStatus
from meeting_scribe_review.core.presenters.summary import SummaryPresenter, SummaryState


class SessionViews(Protocol):
    def show_meetings(self, state: MeetingListState) -> None: ...

    def show_review(self, state: ReviewState) -> None: ...

    def show_rename(self, state: RenameState) -> None: ...

    def show_merge(self, state: MergeState) -> None: ...

    def show_playback(self, state: PlaybackState) -> None: ...

    def show_summary(self, state: SummaryState) -> None: ...

    def show_cloud_confirmation(self, state: ConfirmationState) -> None: ...

    def show_api_key(self, state: ApiKeyState) -> None: ...


class ReviewSession:
    """Also the gateway listener: it hands each service event to the presenters that need it."""

    def __init__(
        self,
        gateway: MeetingsGateway,
        player: SamplePlayer,
        views: SessionViews,
        summaries: SummariesGateway,
        keys: ApiKeyStore,
    ) -> None:
        self._views = views
        self._meetings = MeetingListPresenter(gateway, self._meetings_changed)
        self._review = ReviewPresenter(gateway, views.show_review)
        self._merge = MergePresenter(gateway, views.show_merge, self._applied)
        self._rename = RenamePresenter(gateway, views.show_rename, self._applied, self._merge.merge)
        self._playback = PlaybackPresenter(gateway, player, views.show_playback)
        self._confirmation = CloudConfirmationPresenter(
            views.show_cloud_confirmation, self._cloud_confirmed
        )
        self._summary = SummaryPresenter(summaries, views.show_summary, self._confirmation.ask)
        self._api_key = ApiKeyPresenter(keys, views.show_api_key)
        self._open_id = ""
        gateway.watch(self)
        summaries.watch_summaries(self)

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
        """The window closes: stop the sound, drop kept samples and any open editor."""
        self._open_id = ""
        self._playback.close()
        self._rename.cancel()
        self._merge.cancel()
        self._review.close()
        self._confirmation.cancel()
        self._summary.close()
        self._api_key.cancel()

    def set_api_key(self) -> None:
        self._api_key.edit()

    def submit_api_key(self, raw: str) -> None:
        self._api_key.submit(raw)

    def cancel_api_key(self) -> None:
        self._api_key.cancel()

    def generate_summary(self) -> None:
        """A press on "Generate summary": the only way a summary starts."""
        self._summary.request()

    def cancel_summary(self) -> None:
        self._summary.cancel()

    def confirm_cloud(self) -> None:
        self._confirmation.confirm()

    def cancel_cloud(self) -> None:
        self._confirmation.cancel()

    def play(self, speaker_id: str) -> None:
        """A press on a play button: the only way a sample starts (AC-14)."""
        self._playback.press(self._review.state.meeting_id, speaker_id)

    def stop_playback(self) -> None:
        self._playback.stop()

    def rename(self, speaker_id: str) -> None:
        state = self._review.state
        self._rename.start(state.meeting_id, speaker_id, state.speakers)

    def submit_rename(self, raw: str) -> None:
        self._rename.submit(raw)

    def merge_into_conflict(self) -> None:
        self._rename.merge_into_conflict()

    def edit_again(self) -> None:
        self._rename.edit_again()

    def cancel_rename(self) -> None:
        self._rename.cancel()

    def merge(self, speaker_id: str) -> None:
        state = self._review.state
        self._merge.start(state.meeting_id, speaker_id, state.speakers)

    def confirm_merge(self, target_id: str) -> None:
        self._merge.confirm(target_id)

    def cancel_merge(self) -> None:
        self._merge.cancel()

    def unmerge(self, speaker_id: str) -> None:
        self._merge.unmerge(self._review.state.meeting_id, speaker_id)

    def on_service_available(self, available: bool) -> None:
        self._meetings.on_service_available(available)
        self._review.on_service_available(available)
        self._summary.on_service_available(available)

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:
        self._playback.forget(meeting_id)  # a new run replaces the samples
        self._meetings.on_processing_state_changed(meeting_id, state)
        self._review.on_processing_state_changed(meeting_id, state)
        self._summary.on_processing_state_changed(meeting_id, state)

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:
        self._playback.forget(meeting_id)
        self._meetings.on_processing_finished(meeting_id, state, speaker_count)
        self._review.on_processing_finished(meeting_id, state, speaker_count)
        self._summary.on_processing_finished(meeting_id, state)

    def on_speakers_changed(self, meeting_id: str) -> None:
        self._meetings.on_speakers_changed(meeting_id)
        self._review.on_speakers_changed(meeting_id)
        self._summary.on_speakers_changed(meeting_id)

    def on_summary_progress(self, meeting_id: str, step: int, total_steps: int) -> None:
        self._summary.on_summary_progress(meeting_id, step, total_steps)

    def on_summary_finished(self, meeting_id: str, state: str, error_code: str) -> None:
        self._summary.on_summary_finished(meeting_id, state, error_code)

    def _cloud_confirmed(self, request: ConfirmationRequest) -> None:
        self._summary.send(request)

    def _applied(self, meeting_id: str, speakers: Sequence[Speaker]) -> None:
        self._review.apply_speakers(meeting_id, speakers)

    def _meetings_changed(self, state: MeetingListState) -> None:
        self._views.show_meetings(state)
        if state.status is MeetingListStatus.ERROR:
            return  # keep the open review; its own error comes from the review presenter
        if state.selected_id == self._open_id:
            return
        self._open_id = state.selected_id
        self._playback.meeting_changed(state.selected_id)
        self._confirmation.cancel()  # a confirmation belongs to the meeting it was asked for
        if not state.selected_id:
            self._review.close()
            self._summary.close()
            return
        row = next((row for row in state.rows if row.id == state.selected_id), None)
        if row is None:
            self._open_id = ""
            self._review.close()
            self._summary.close()
            return
        self._review.open(row.id, row.state)
        self._summary.open(row.id, row.state)
