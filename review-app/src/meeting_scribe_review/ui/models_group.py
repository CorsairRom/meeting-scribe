"""The "Speech models" group of the Preferences dialog: the token, the terms link, the download.

Declarative glue over ``ModelsState``. The token field is write-only: it is emptied the moment it
is submitted (the state holds only "stored" or not) and never filled from anywhere. The group acts
at once (it is not part of Save): storing the token, Download models and Cancel go straight to the
presenter, which owns every rule about when each is possible.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, GLib, Gtk

from meeting_scribe_review.core.errors import command_hint
from meeting_scribe_review.core.models import KeyStatus
from meeting_scribe_review.core.presenters.models import TERMS_TEXT, ModelsState

TITLE = "Speech models"
DESCRIPTION = "Speaker identification needs two models, downloaded once (about 1.7 GB)."
ENTRY_TITLE = "Hugging Face token"
TERMS_TITLE = "Accept the pyannote terms"
DOWNLOAD_LABEL = "Download models"
CANCEL_LABEL = "Cancel"
RETRY_LABEL = "Try again"
_PULSE_MILLISECONDS = 150
_BAR_WIDTH = 160


@dataclass(frozen=True, slots=True)
class ModelsActions:
    submit_token: Callable[[str], None]
    download: Callable[[], None]
    cancel: Callable[[], None]
    retry: Callable[[], None]
    open_terms: Callable[[], None]


class ModelsGroup(Adw.PreferencesGroup):
    def __init__(self, actions: ModelsActions) -> None:
        super().__init__(title=TITLE, description=DESCRIPTION)
        self._pulse_id = 0

        self._terms = Adw.ActionRow(
            title=TERMS_TITLE, subtitle=TERMS_TEXT, activatable=True, use_markup=False
        )
        self._terms.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        self._terms.connect("activated", lambda _row: actions.open_terms())

        # Write-only: the apply handler hands the text over and empties the field at once.
        self._entry = Adw.PasswordEntryRow(title=ENTRY_TITLE, show_apply_button=True)
        self._stored = Gtk.Label(valign=Gtk.Align.CENTER)
        self._stored.add_css_class("dim-label")
        self._entry.add_suffix(self._stored)
        self._entry.connect("apply", lambda row: self._submit(row, actions.submit_token))

        self._status = Adw.ActionRow(use_markup=False)
        self._cancel = Gtk.Button(label=CANCEL_LABEL, valign=Gtk.Align.CENTER)
        self._cancel.connect("clicked", lambda _button: actions.cancel())
        self._retry = Gtk.Button(label=RETRY_LABEL, valign=Gtk.Align.CENTER)
        self._retry.connect("clicked", lambda _button: actions.retry())
        self._download = Gtk.Button(label=DOWNLOAD_LABEL, valign=Gtk.Align.CENTER)
        self._download.add_css_class("suggested-action")
        self._download.connect("clicked", lambda _button: actions.download())
        self._status.add_suffix(self._retry)
        self._status.add_suffix(self._cancel)
        self._status.add_suffix(self._download)

        self._bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=_BAR_WIDTH)
        self._progress = Adw.ActionRow(use_markup=False)
        self._progress.add_suffix(self._bar)
        self._progress.set_visible(False)

        for row in (self._terms, self._entry, self._status, self._progress):
            self.add(row)
        self.connect("unrealize", lambda _group: self._stop_pulse())

    @staticmethod
    def _submit(row: Adw.PasswordEntryRow, submit: Callable[[str], None]) -> None:
        text = row.get_text()
        row.set_text("")
        submit(text)

    def update(self, state: ModelsState) -> None:
        self._status.set_title(state.status_text)
        self._status.set_subtitle(
            "\n".join(line for line in (state.detail, command_hint(state.hint)) if line)
        )
        self._download.set_sensitive(state.can_download)
        self._cancel.set_visible(state.can_cancel)
        self._retry.set_visible(state.can_retry)
        self._entry.set_sensitive(state.can_save_token)
        self._stored.set_label(state.token_text)
        self._stored.set_visible(state.token_status is not KeyStatus.UNKNOWN)
        notes = [line for line in (state.token_problem, state.token_message) if line]
        self.set_description(GLib.markup_escape_text("\n".join([DESCRIPTION, *notes])))
        self._sync_progress(state)

    def _sync_progress(self, state: ModelsState) -> None:
        self._progress.set_visible(state.show_progress)
        self._progress.set_title(state.progress_text)
        pulsing = state.show_progress and state.progress_fraction is None
        if state.progress_fraction is not None:
            self._bar.set_fraction(state.progress_fraction)
        if pulsing and not self._pulse_id:
            self._pulse_id = GLib.timeout_add(_PULSE_MILLISECONDS, self._pulse)
        elif not pulsing:
            self._stop_pulse()

    def _pulse(self) -> bool:
        self._bar.pulse()
        return GLib.SOURCE_CONTINUE

    def _stop_pulse(self) -> None:
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = 0
