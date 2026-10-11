"""The review window: ``Adw.NavigationSplitView`` with the meetings sidebar and the review pane.

Declarative glue only. Which meeting is open and what refreshes on a service event is decided by
``ReviewSession``; this class shows the states it publishes.
"""

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.presenters.meeting_list import MeetingListState, MeetingListStatus
from meeting_scribe_review.core.presenters.review import ReviewState, ReviewStatus
from meeting_scribe_review.core.session import ReviewSession
from meeting_scribe_review.ui.dbus_gateway import DBusGateway
from meeting_scribe_review.ui.meeting_sidebar import MeetingSidebar
from meeting_scribe_review.ui.status_view import StatusView
from meeting_scribe_review.ui.transcript_view import TranscriptView

TITLE = "Meeting Scribe"
DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700
COLLAPSE_BELOW = "max-width: 600sp"
_REVIEW = "review"
_STATUS = "status"


def _run_hint(hint: str) -> str:
    return f"Run: {hint}" if hint else ""


class ReviewWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, gateway: DBusGateway, meeting_id: str) -> None:
        super().__init__(
            application=application,
            title=TITLE,
            default_width=DEFAULT_WIDTH,
            default_height=DEFAULT_HEIGHT,
        )
        self._gateway = gateway
        self._meetings = MeetingListState()
        self._review = ReviewState()

        self._sidebar = MeetingSidebar(
            lambda selected: self._session.select(selected), lambda: self._session.retry()
        )
        self._transcript = TranscriptView()
        self._status = StatusView(lambda: self._session.retry())
        self._banner = Adw.Banner()
        self._panes = Gtk.Stack()
        self._panes.add_named(self._transcript, _REVIEW)
        self._panes.add_named(self._status, _STATUS)

        split = Adw.NavigationSplitView(
            sidebar=self._page("Meetings", self._sidebar),
            content=self._page(TITLE, self._panes, banner=self._banner),
        )
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(COLLAPSE_BELOW))
        breakpoint_.add_setter(split, "collapsed", True)
        self.add_breakpoint(breakpoint_)
        self.set_content(split)
        self.connect("close-request", self._on_close_request)

        self._session = ReviewSession(gateway, self)
        self._session.start(meeting_id)

    def open_meeting(self, meeting_id: str) -> None:
        """Show another meeting in this window (a second launch with ``--meeting``)."""
        self._session.start(meeting_id)

    def show_meetings(self, state: MeetingListState) -> None:
        self._meetings = state
        self._sidebar.update(state)
        self._refresh()

    def show_review(self, state: ReviewState) -> None:
        self._review = state
        self._refresh()

    @staticmethod
    def _page(
        title: str, child: Gtk.Widget, banner: Adw.Banner | None = None
    ) -> Adw.NavigationPage:
        view = Adw.ToolbarView(content=child)
        view.add_top_bar(Adw.HeaderBar())
        if banner is not None:
            view.add_top_bar(banner)
        return Adw.NavigationPage.new(view, title)

    def _refresh(self) -> None:
        meetings, review = self._meetings, self._review
        self._banner.set_revealed(False)
        if meetings.status is MeetingListStatus.ERROR:
            self._message(meetings.message, _run_hint(meetings.detail), retry=True)
        elif meetings.status is MeetingListStatus.EMPTY:
            self._message(meetings.message, meetings.detail)
        elif review.status is ReviewStatus.READY:
            self._transcript.update(review.transcript)
            self._panes.set_visible_child_name(_REVIEW)
        elif review.status is ReviewStatus.READ_ONLY:
            self._banner.set_title(review.banner)
            self._banner.set_revealed(True)
            self._message("Review is not available for this meeting")
        elif review.status is ReviewStatus.ERROR:
            self._message(review.message, _run_hint(review.hint), retry=True)
        elif review.status is ReviewStatus.LOADING:
            self._message("Loading the meeting")
        else:
            self._message("Select a meeting")

    def _message(self, title: str, description: str = "", *, retry: bool = False) -> None:
        icon = "dialog-warning-symbolic" if retry else ""
        self._status.show_message(title, description, icon, retry=retry)
        self._panes.set_visible_child_name(_STATUS)

    def _on_close_request(self, _window: Adw.ApplicationWindow) -> bool:
        self._session.close()
        self._gateway.close()
        return False
