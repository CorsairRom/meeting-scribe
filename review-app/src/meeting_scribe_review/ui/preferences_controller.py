"""Opens the Preferences dialog over a window and feeds it the session's states."""

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from meeting_scribe_review.core.preferences_session import PreferencesSession
from meeting_scribe_review.core.presenters.api_key import ApiKeyState
from meeting_scribe_review.core.presenters.models import TERMS_URL, ModelsPresenter, ModelsState
from meeting_scribe_review.core.presenters.preferences import PreferencesState
from meeting_scribe_review.ui.api_key_dialog import ApiKeyDialog
from meeting_scribe_review.ui.dbus_gateway import DBusGateway
from meeting_scribe_review.ui.key_store import SecretKeyStore
from meeting_scribe_review.ui.models_group import ModelsActions
from meeting_scribe_review.ui.preferences_dialog import PreferencesActions, PreferencesDialog

TERMS_FAILED_TEXT = f"Could not open the browser. Open {TERMS_URL} yourself."


def _after(seconds: float, call: Callable[[], None]) -> None:
    def fire() -> bool:
        call()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(int(seconds), fire)


class PreferencesController:
    """One per window: the session lives as long as the window, a dialog per opening."""

    def __init__(self, gateway: DBusGateway, keys: SecretKeyStore, tokens: SecretKeyStore) -> None:
        self._session = PreferencesSession(gateway, keys, self, _after)
        self._models = ModelsPresenter(gateway, tokens, self.show_models)
        self._parent: Gtk.Window | None = None
        self._dialog: PreferencesDialog | None = None
        self._key_dialog: ApiKeyDialog | None = None

    def shutdown(self) -> None:
        """The window closes: stop listening to the service."""
        self._session.dispose()
        self._models.dispose()

    def open(self, parent: Gtk.Window) -> None:
        """Present the dialog over ``parent``; a second call raises the one that is open."""
        if self._dialog is not None:
            self._dialog.present(parent)
            return
        session = self._session
        self._parent = parent
        dialog = PreferencesDialog(
            PreferencesActions(
                edit=session.edit,
                save=session.save,
                restart=session.restart,
                refresh_models=session.refresh_models,
                set_key=session.set_api_key,
                remove_key=session.remove_key,
                models=ModelsActions(
                    submit_token=self._models.submit_token,
                    download=self._models.download,
                    cancel=self._models.cancel,
                    retry=self._models.retry,
                    open_terms=self._open_terms,
                ),
            ),
            parent,
        )
        dialog.connect("closed", self._closed)
        self._dialog = dialog
        self._key_dialog = ApiKeyDialog(dialog, session.submit_api_key, session.cancel_api_key)
        dialog.present(parent)
        session.open()
        self._models.open()

    def show_preferences(self, state: PreferencesState) -> None:
        if self._dialog is None:
            return
        self._dialog.update(state)
        if state.saved:
            self._dialog.close()

    def show_models(self, state: ModelsState) -> None:
        if self._dialog is not None:
            self._dialog.update_models(state)

    def _open_terms(self) -> None:
        """Open the terms page with the system handler; the user accepts them there."""

        def launched(launcher: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                if self._dialog is not None:  # the page is public: the toast names it
                    self._dialog.add_toast(Adw.Toast(title=TERMS_FAILED_TEXT))

        Gtk.UriLauncher.new(TERMS_URL).launch(self._parent, None, launched)

    def show_api_key(self, state: ApiKeyState) -> None:
        if self._key_dialog is not None:
            self._key_dialog.show(state)
        if state.message and self._dialog is not None:
            self._dialog.add_toast(Adw.Toast(title=state.message))

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._dialog = None
        self._key_dialog = None
        self._session.close()
        self._models.close()
