"""The "Set API key" dialog, shared by the review window and the Preferences dialog.

The key goes from the field to the ``submit`` callback and nowhere else (BR-005).
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.presenters.api_key import ApiKeyState, ApiKeyStatus
from meeting_scribe_review.ui.dialogs import AlertHost

_CANCEL = "cancel"
_SAVE = "save"


class ApiKeyDialog(AlertHost):
    def __init__(
        self, parent: Gtk.Widget, submit: Callable[[str], None], cancel: Callable[[], None]
    ) -> None:
        super().__init__(parent)
        self._submit = submit
        self._cancel = cancel

    def show(self, state: ApiKeyState) -> None:
        if state.status is ApiKeyStatus.EDITING:
            self._edit(state)
        elif state.status is not ApiKeyStatus.SAVING:
            self._dismiss()

    def _edit(self, state: ApiKeyState) -> None:
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
        self._present(dialog, {_SAVE: lambda: self._submit(entry.get_text())}, self._cancel)
        entry.grab_focus()
