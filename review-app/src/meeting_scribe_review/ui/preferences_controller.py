"""Opens the Preferences dialog over a window and feeds it the session's states."""

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.preferences_session import PreferencesSession
from meeting_scribe_review.core.presenters.api_key import ApiKeyState
from meeting_scribe_review.core.presenters.preferences import PreferencesState
from meeting_scribe_review.ui.api_key_dialog import ApiKeyDialog
from meeting_scribe_review.ui.dbus_gateway import DBusGateway
from meeting_scribe_review.ui.key_store import SecretKeyStore
from meeting_scribe_review.ui.preferences_dialog import PreferencesActions, PreferencesDialog


class PreferencesController:
    """One per window: the session lives as long as the window, a dialog per opening."""

    def __init__(self, gateway: DBusGateway, keys: SecretKeyStore) -> None:
        self._session = PreferencesSession(gateway, keys, self)
        self._dialog: PreferencesDialog | None = None
        self._key_dialog: ApiKeyDialog | None = None

    def open(self, parent: Gtk.Window) -> None:
        """Present the dialog over ``parent``; a second call raises the one that is open."""
        if self._dialog is not None:
            self._dialog.present(parent)
            return
        session = self._session
        dialog = PreferencesDialog(
            PreferencesActions(
                edit=session.edit,
                save=session.save,
                restart=session.restart,
                set_key=session.set_api_key,
                remove_key=session.remove_key,
            ),
            parent,
        )
        dialog.connect("closed", self._closed)
        self._dialog = dialog
        self._key_dialog = ApiKeyDialog(dialog, session.submit_api_key, session.cancel_api_key)
        dialog.present(parent)
        session.open()

    def show_preferences(self, state: PreferencesState) -> None:
        if self._dialog is None:
            return
        self._dialog.update(state)
        if state.saved:
            self._dialog.close()

    def show_api_key(self, state: ApiKeyState) -> None:
        if self._key_dialog is not None:
            self._key_dialog.show(state)
        if state.message and self._dialog is not None:
            self._dialog.add_toast(Adw.Toast(title=state.message))

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._dialog = None
        self._key_dialog = None
        self._session.close()
