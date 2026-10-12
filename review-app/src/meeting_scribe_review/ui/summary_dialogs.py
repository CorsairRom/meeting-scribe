"""The cloud confirmation and the "Set API key" dialogs, driven by presenter states."""

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.presenters.api_key import ApiKeyState
from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationState
from meeting_scribe_review.core.session import ReviewSession
from meeting_scribe_review.ui.api_key_dialog import ApiKeyDialog
from meeting_scribe_review.ui.dialogs import AlertHost

_CANCEL = "cancel"
_SEND = "send"


class SummaryDialogs(AlertHost):
    def __init__(self, parent: Gtk.Widget, session: ReviewSession) -> None:
        super().__init__(parent)
        self._session = session
        self._key_dialog = ApiKeyDialog(parent, session.submit_api_key, session.cancel_api_key)

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
        self._key_dialog.show(state)
