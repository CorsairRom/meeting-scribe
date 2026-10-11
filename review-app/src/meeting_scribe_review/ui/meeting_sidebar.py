"""The meetings sidebar: one row per meeting, or a status page when there is nothing to list."""

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from meeting_scribe_review.core.formatting import format_speaker_count
from meeting_scribe_review.core.presenters.meeting_list import (
    MeetingListState,
    MeetingListStatus,
    MeetingRow,
)
from meeting_scribe_review.ui.status_view import StatusView

UNKNOWN_DATE = "Unknown date"
_LIST = "list"
_STATUS = "status"


def _when(row: MeetingRow) -> str:
    """The start time in the user's locale and time zone; never fails on an odd value."""
    try:
        moment = GLib.DateTime.new_from_unix_utc(int(row.started_at.timestamp()))
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_DATE
    local = moment.to_local() if moment else None
    text = local.format("%x %R") if local else None
    return text or UNKNOWN_DATE


def _subtitle(row: MeetingRow) -> str:
    parts = [row.duration_text, row.state_label]
    if row.reviewable:
        parts.append(format_speaker_count(row.speaker_count))
    return " · ".join(parts)


class MeetingSidebar(Gtk.Stack):
    def __init__(self, on_select: Callable[[str], None], on_retry: Callable[[], None]) -> None:
        super().__init__()
        self._on_select = on_select
        self._syncing = False
        self._ids: list[str] = []
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-selected", self._row_selected)
        scrolled = Gtk.ScrolledWindow(child=self._list, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._status = StatusView(on_retry)
        self.add_named(scrolled, _LIST)
        self.add_named(self._status, _STATUS)
        self._status.show_message("Loading meetings")

    def update(self, state: MeetingListState) -> None:
        if state.status is MeetingListStatus.ERROR:
            self._status.show_message(
                state.message, state.detail, "dialog-warning-symbolic", retry=True
            )
            self.set_visible_child_name(_STATUS)
            return
        if state.status is MeetingListStatus.LOADING:
            self._status.show_message("Loading meetings")
            self.set_visible_child_name(_STATUS)
            return
        self._fill(state)
        if state.status is MeetingListStatus.EMPTY and not state.rows:
            self._status.show_message(state.message, icon="audio-input-microphone-symbolic")
            self.set_visible_child_name(_STATUS)
        else:
            self.set_visible_child_name(_LIST)

    def _fill(self, state: MeetingListState) -> None:
        self._syncing = True
        try:
            self._list.remove_all()
            self._ids = [row.id for row in state.rows]
            for row in state.rows:
                widget = Adw.ActionRow(title=_when(row), subtitle=_subtitle(row), use_markup=False)
                if not row.reviewable:
                    widget.add_css_class("dim-label")
                self._list.append(widget)
            chosen = self._ids.index(state.selected_id) if state.selected_id in self._ids else -1
            self._list.select_row(self._list.get_row_at_index(chosen) if chosen >= 0 else None)
        finally:
            self._syncing = False

    def _row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._syncing or row is None:
            return
        self._on_select(self._ids[row.get_index()])
