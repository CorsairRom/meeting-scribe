"""The cloud confirmation and the "Set API key" dialogs, driven by presenter states."""

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.presenters.api_key import ApiKeyState, ApiKeyStatus
from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationState
from meeting_scribe_review.core.session import ReviewSession
from meeting_scribe_review.ui.dialogs import AlertHost

_CANCEL = "cancel"
_SEND = "send"
_SAVE = "save"


class SummaryDialogs(AlertHost):
    def __init__(self, parent: Gtk.Widget, session: ReviewSession) -> None:
        super().__init__(parent)
        self._session = session

    def show_confirmation(self, state: ConfirmationState) -> None:
        """Cancel is the default and the response of Escape; only the explicit action sends."""
        if not state.open:
            self._dismiss()
            return
        dialog = Adw.AlertDialog(heading=state.heading, body=state.body)
        dialog.add_response(_CANCEL, state.cancel_label)
        dialog.add_response(_SEND, state.confirm_label)
        dialog.set_response_appearance(_SEND, Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response(_CANCEL)
        dialog.set_close_response(_CANCEL)
        self._present(dialog, {_SEND: self._session.confirm_cloud}, self._session.cancel_cloud)

    def show_api_key(self, state: ApiKeyState) -> None:
        if state.status is ApiKeyStatus.EDITING:
            self._api_key(state)
        elif state.status is not ApiKeyStatus.SAVING:
            self._dismiss()

    def _api_key(self, state: ApiKeyState) -> None:
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["API key"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(entry)
        if state.problem:
            problem = Gtk.Label(label=state.problem, xalign=0, wrap=True)
            problem.add_css_class("error")
            box.append(problem)
        dialog = Adw.AlertDialog(
            heading="Set API key",
            body="The key is stored in your GNOME Keyring. It is never sent to the service.",
        )
        dialog.set_extra_child(box)
        dialog.add_response(_CANCEL, "Cancel")
        dialog.add_response(_SAVE, "Save")
        dialog.set_response_appearance(_SAVE, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(_SAVE)
        dialog.set_close_response(_CANCEL)
        self._present(
            dialog,
            {_SAVE: lambda: self._session.submit_api_key(entry.get_text())},
            self._session.cancel_api_key,
        )
        entry.grab_focus()
