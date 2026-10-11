"""Copy the summary and "Save a copy": the clipboard and a user-chosen file, never a fixed path."""

from collections.abc import Callable

from gi.repository import Gdk, Gio, GLib, Gtk

from meeting_scribe_review.core.presenters.summary import (
    COPIED_TEXT,
    SAVE_FAILED_TEXT,
    SAVED_TEXT,
    SummaryState,
)


class SummaryExport:
    """The text is read from the state the user sees; nothing is logged."""

    def __init__(self, parent: Gtk.Widget, toast: Callable[[str], None]) -> None:
        self._parent = parent
        self._toast = toast

    def copy(self, state: SummaryState) -> None:
        if not state.can_copy:
            return
        provider = Gdk.ContentProvider.new_for_value(state.text)
        self._parent.get_clipboard().set_content(provider)
        self._toast(COPIED_TEXT)

    def save(self, state: SummaryState) -> None:
        if not state.can_copy:
            return
        dialog = Gtk.FileDialog(title="Save a copy of the summary", initial_name=state.filename)
        dialog.save(
            self._parent.get_root(),
            None,
            lambda source, result: self._chosen(source, result, state.text),
        )

    def _chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult, text: str) -> None:
        try:
            target = dialog.save_finish(result)
        except GLib.Error:
            return  # the user closed the chooser
        target.replace_contents_bytes_async(
            GLib.Bytes.new(text.encode("utf-8")),
            None,
            False,
            Gio.FileCreateFlags.REPLACE_DESTINATION,
            None,
            self._written,
        )

    def _written(self, target: Gio.File, result: Gio.AsyncResult) -> None:
        try:
            target.replace_contents_finish(result)
        except GLib.Error:
            self._toast(SAVE_FAILED_TEXT)
            return
        self._toast(SAVED_TEXT)
