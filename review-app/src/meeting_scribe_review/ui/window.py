"""The review window: ``Adw.NavigationSplitView`` with the meetings sidebar and the review pane.

Declarative glue only. Which meeting is open and what refreshes on a service event is decided by
``ReviewSession``; this class shows the states it publishes.
"""

from gi.repository import Adw, Gio, Gtk

from meeting_scribe_review.core.errors import command_hint
from meeting_scribe_review.core.presenters.api_key import ApiKeyState
from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationState
from meeting_scribe_review.core.presenters.meeting_list import MeetingListState, MeetingListStatus
from meeting_scribe_review.core.presenters.merge import MergeState
from meeting_scribe_review.core.presenters.playback import PlaybackState
from meeting_scribe_review.core.presenters.rename import RenameState
from meeting_scribe_review.core.presenters.review import ReviewState, ReviewStatus
from meeting_scribe_review.core.presenters.summary import SummaryState
from meeting_scribe_review.core.session import ReviewSession, SummarySettingsListener
from meeting_scribe_review.ui.dbus_gateway import DBusGateway
from meeting_scribe_review.ui.dialogs import SpeakerDialogs
from meeting_scribe_review.ui.key_store import SecretKeyStore
from meeting_scribe_review.ui.media_player import MediaPlayer
from meeting_scribe_review.ui.meeting_sidebar import MeetingSidebar
from meeting_scribe_review.ui.preferences_controller import PreferencesController
from meeting_scribe_review.ui.speaker_row import SpeakerActions, SpeakersSection
from meeting_scribe_review.ui.status_view import StatusView
from meeting_scribe_review.ui.summary_dialogs import SummaryDialogs
from meeting_scribe_review.ui.summary_export import SummaryExport
from meeting_scribe_review.ui.summary_view import SummaryActions, SummaryView
from meeting_scribe_review.ui.transcript_view import TranscriptView

TITLE = "Meeting Scribe"
DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700
SPEAKERS_MAX_HEIGHT = 300
COLLAPSE_BELOW = "max-width: 600sp"
_REVIEW = "review"
_STATUS = "status"


class ReviewWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        application: Adw.Application,
        gateway: DBusGateway,
        player: MediaPlayer,
        keys: SecretKeyStore,
        meeting_id: str,
    ) -> None:
        super().__init__(
            application=application,
            title=TITLE,
            default_width=DEFAULT_WIDTH,
            default_height=DEFAULT_HEIGHT,
        )
        self._gateway = gateway
        self._preferences = PreferencesController(gateway, keys)
        action = Gio.SimpleAction.new("preferences", None)
        action.connect("activate", lambda _action, _param: self.open_preferences())
        self.add_action(action)
        self._meetings = MeetingListState()
        self._review = ReviewState()
        self._summary_state = SummaryState()

        self._sidebar = MeetingSidebar(
            lambda selected: self._session.select(selected), lambda: self._session.retry()
        )
        self._transcript = TranscriptView()
        self._speakers = SpeakersSection(
            SpeakerActions(
                play=lambda speaker_id: self._session.play(speaker_id),
                rename=lambda speaker_id: self._session.rename(speaker_id),
                merge=lambda speaker_id: self._session.merge(speaker_id),
                unmerge=lambda speaker_id: self._session.unmerge(speaker_id),
            )
        )
        self._last_toast = ""
        self._status = StatusView(lambda: self._session.retry())
        self._banner = Adw.Banner(use_markup=False)
        self._summary = SummaryView(
            SummaryActions(
                generate=lambda: self._session.generate_summary(),
                cancel=lambda: self._session.cancel_summary(),
                open_preferences=lambda: self.open_preferences(),
                copy=lambda: self._export.copy(self._summary_state),
                save=lambda: self._export.save(self._summary_state),
            )
        )
        self._tabs = Adw.ViewStack()
        self._tabs.add_titled(self._review_box(), "review", "Review")
        self._tabs.add_titled(self._summary, "summary", "Summary")
        self._switcher = Adw.ViewSwitcher(stack=self._tabs, policy=Adw.ViewSwitcherPolicy.WIDE)
        self._panes = Gtk.Stack()
        self._panes.add_named(self._tabs, _REVIEW)
        self._panes.add_named(self._status, _STATUS)
        self._toasts = Adw.ToastOverlay(child=self._panes)

        split = Adw.NavigationSplitView(
            sidebar=self._page("Meetings", self._sidebar),
            content=self._page(
                TITLE,
                self._toasts,
                banner=self._banner,
                title_widget=self._switcher,
                menu=self._primary_menu(),
            ),
        )
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(COLLAPSE_BELOW))
        breakpoint_.add_setter(split, "collapsed", True)
        self.add_breakpoint(breakpoint_)
        self.set_content(split)
        self.connect("close-request", self._on_close_request)

        self._session = ReviewSession(gateway, player, self, summaries=gateway, keys=keys)
        self._settings_listener = SummarySettingsListener(self._session)
        gateway.watch_settings(self._settings_listener)
        self._dialogs = SpeakerDialogs(self, self._session)
        self._summary_dialogs = SummaryDialogs(self, self._session)
        self._export = SummaryExport(self, self._toast)
        self._session.start(meeting_id)

    def open_meeting(self, meeting_id: str) -> None:
        """Show another meeting in this window (a second launch with ``--meeting``)."""
        self._session.start(meeting_id)

    def open_preferences(self) -> None:
        """Show the Preferences dialog over this window (the menu entry and ``--preferences``)."""
        self._preferences.open(self)

    def show_meetings(self, state: MeetingListState) -> None:
        self._meetings = state
        self._sidebar.update(state)
        self._refresh()

    def show_review(self, state: ReviewState) -> None:
        self._review = state
        if state.status is ReviewStatus.READY:
            self._speakers.update(state)
            self._transcript.update(state.transcript)
        self._refresh()

    def show_rename(self, state: RenameState) -> None:
        self._dialogs.show_rename(state, self._review)

    def show_merge(self, state: MergeState) -> None:
        self._dialogs.show_merge(state, self._review)

    def show_playback(self, state: PlaybackState) -> None:
        self._speakers.update_playback(state)
        if state.message and state.message != self._last_toast:
            self._toasts.add_toast(Adw.Toast(title=state.message))
        self._last_toast = state.message

    def show_summary(self, state: SummaryState) -> None:
        self._summary_state = state
        self._summary.update(state)

    def show_cloud_confirmation(self, state: ConfirmationState) -> None:
        self._summary_dialogs.show_confirmation(state)

    def show_api_key(self, state: ApiKeyState) -> None:
        self._summary_dialogs.show_api_key(state)
        if state.message:
            self._toast(state.message)

    def _toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text))

    def _review_box(self) -> Gtk.Box:
        """Speakers on top (a short scrolling list), the transcript below it."""
        speakers = Gtk.ScrolledWindow(
            child=self._speakers,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=SPEAKERS_MAX_HEIGHT,
        )
        heading = Gtk.Label(label="Transcript", xalign=0, margin_start=12, margin_top=12)
        heading.add_css_class("title-4")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for child in (speakers, Gtk.Separator(), heading, self._transcript):
            box.append(child)
        return box

    @staticmethod
    def _primary_menu() -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        return Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, primary=True, tooltip_text="Main menu"
        )

    @staticmethod
    def _page(
        title: str,
        child: Gtk.Widget,
        banner: Adw.Banner | None = None,
        title_widget: Gtk.Widget | None = None,
        menu: Gtk.Widget | None = None,
    ) -> Adw.NavigationPage:
        view = Adw.ToolbarView(content=child)
        header = Adw.HeaderBar()
        if title_widget is not None:
            header.set_title_widget(title_widget)
        if menu is not None:
            header.pack_end(menu)
        view.add_top_bar(header)
        if banner is not None:
            view.add_top_bar(banner)
        return Adw.NavigationPage.new(view, title)

    def _refresh(self) -> None:
        meetings, review = self._meetings, self._review
        self._banner.set_revealed(False)
        self._switcher.set_visible(review.status is ReviewStatus.READY)
        if meetings.status is MeetingListStatus.ERROR:
            self._message(meetings.message, command_hint(meetings.detail), retry=True)
        elif meetings.status is MeetingListStatus.EMPTY:
            self._message(meetings.message, meetings.detail)
        elif review.status is ReviewStatus.READY:
            self._panes.set_visible_child_name(_REVIEW)
        elif review.status is ReviewStatus.READ_ONLY:
            self._banner.set_title(review.banner)
            self._banner.set_revealed(True)
            self._message("Review is not available for this meeting")
        elif review.status is ReviewStatus.ERROR:
            self._message(review.message, command_hint(review.hint), retry=True)
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
        self._gateway.unwatch_settings(self._settings_listener)
        self._preferences.shutdown()
        self._gateway.close()
        return False
