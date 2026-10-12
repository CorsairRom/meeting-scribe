"""The Preferences dialog: ``Adw.PreferencesDialog`` over the presenter's state.

Declarative glue only. Edits are reported as they happen and staged by the presenter; nothing is
saved until the Save button is pressed. Every widget is synced from the state under a guard, so
setting a value never reads back as a user edit. The cloud key is never shown, only whether it is
stored, and the key editor is a separate dialog (BR-005).
"""

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gio, GLib, Gtk

from meeting_scribe_review.core.errors import command_hint
from meeting_scribe_review.core.presenters.preferences import (
    CLOUD_MODEL,
    LANGUAGE,
    LOCAL_MODEL,
    MEETINGS_DIR,
    PROVIDER,
    FieldState,
    PreferencesPhase,
    PreferencesState,
)

TITLE = "Preferences"
SAVE_LABEL = "Save"
SAVING_LABEL = "Saving..."


@dataclass(frozen=True, slots=True)
class PreferencesActions:
    edit: Callable[[str, str], None]
    save: Callable[[], None]
    restart: Callable[[], None]
    set_key: Callable[[], None]
    remove_key: Callable[[], None]


def _button(label: str, handler: Callable[[], None], *, css: str = "") -> Gtk.Button:
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if css:
        button.add_css_class(css)
    button.connect("clicked", lambda _button: handler())
    return button


def _describe(group: Adw.PreferencesGroup, lines: list[str]) -> None:
    text = "\n".join(line for line in lines if line)
    group.set_description(GLib.markup_escape_text(text))


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, actions: PreferencesActions, parent: Gtk.Window) -> None:
        super().__init__(title=TITLE, search_enabled=False)
        self._actions = actions
        self._parent_window = parent
        self._syncing = False
        self._options: dict[str, tuple[tuple[str, str], ...]] = {}
        self._state = PreferencesState()

        self._provider = self._combo(PROVIDER, "Provider")
        self._language = self._combo(LANGUAGE, "Summary language")
        self._local = self._entry(LOCAL_MODEL, "Model")
        self._cloud = self._entry(CLOUD_MODEL, "Model")
        self._key_row = Adw.ActionRow(title="API key", use_markup=False)
        self._set_key = _button("Set API key...", actions.set_key)
        self._remove_key = _button("Remove", actions.remove_key, css="destructive-action")
        self._key_row.add_suffix(self._remove_key)
        self._key_row.add_suffix(self._set_key)
        self._folder = Adw.ActionRow(title="Meetings folder", use_markup=False)
        self._folder.add_suffix(_button("Choose...", self._choose_folder))
        self._restart_row = Adw.ActionRow(use_markup=False)
        self._restart_button = _button("Restart service", actions.restart)
        self._restart_row.add_suffix(self._restart_button)
        self._status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self._save = _button(SAVE_LABEL, actions.save, css="suggested-action")
        self._save.add_css_class("pill")
        self._save.set_halign(Gtk.Align.CENTER)

        self._notice = self._group(None, self._status)
        self._summaries = self._group("Summaries", self._provider, self._language)
        self._local_group = self._group("Local model", self._local)
        self._cloud_group = self._group("Cloud (OpenCode Go)", self._cloud, self._key_row)
        self._restart_group = self._group(None, self._restart_row)
        self._recordings = self._group("Recordings", self._folder)
        page = Adw.PreferencesPage()
        for group in (
            self._notice,
            self._summaries,
            self._local_group,
            self._cloud_group,
            self._recordings,
            self._restart_group,
            self._group(None, self._save),
        ):
            page.add(group)
        self.add(page)

    # -- state -> widgets -------------------------------------------------------------------

    def update(self, state: PreferencesState) -> None:
        self._state = state
        ready = state.phase is PreferencesPhase.READY
        self._syncing = True
        try:
            self._sync_fields(state, ready=ready)
            self._sync_chrome(state, ready=ready)
        finally:
            self._syncing = False

    def _sync_fields(self, state: PreferencesState, *, ready: bool) -> None:
        if not state.fields:
            return
        for key, row in ((PROVIDER, self._provider), (LANGUAGE, self._language)):
            field = state.field(key)
            self._sync_combo(key, row, field)
            row.set_sensitive(ready and not field.locked)
        for key, entry in ((LOCAL_MODEL, self._local), (CLOUD_MODEL, self._cloud)):
            field = state.field(key)
            if entry.get_text() != field.value:
                entry.set_text(field.value)
            entry.set_sensitive(ready and not field.locked)
        folder = state.field(MEETINGS_DIR)
        self._folder.set_subtitle(folder.value)
        self._folder.set_sensitive(ready and not folder.locked)
        _describe(self._summaries, self._notes(state, PROVIDER, LANGUAGE))
        _describe(self._local_group, self._notes(state, LOCAL_MODEL))
        _describe(self._cloud_group, [state.cloud_note, *self._notes(state, CLOUD_MODEL)])
        _describe(self._recordings, [state.folder_note, *self._notes(state, MEETINGS_DIR)])

    @staticmethod
    def _notes(state: PreferencesState, *keys: str) -> list[str]:
        """A line per field that is locked, not saved against a newer value, or has a problem."""
        lines: list[str] = []
        for key in keys:
            field = state.field(key)
            texts = (field.error, field.lock_text, _stale_text(field))
            lines.extend(f"{field.label}: {text}" for text in texts if text)
        return lines

    def _sync_chrome(self, state: PreferencesState, *, ready: bool) -> None:
        problem = state.message
        hint = command_hint(state.hint)
        self._status.set_label("\n".join(line for line in (problem, hint) if line))
        self._notice.set_visible(bool(problem))
        self._local_group.set_visible(bool(state.fields) and not state.show_cloud)
        self._cloud_group.set_visible(state.show_cloud)
        self._key_row.set_subtitle(state.key_text)
        self._set_key.set_sensitive(ready)
        self._remove_key.set_visible(state.can_remove_key)
        self._restart_row.set_title(state.restart_text)
        self._restart_group.set_visible(bool(state.restart_text))
        self._restart_button.set_sensitive(state.can_restart)
        self._save.set_sensitive(state.can_save)
        self._save.set_label(SAVING_LABEL if state.saving else SAVE_LABEL)

    def _sync_combo(self, key: str, row: Adw.ComboRow, field: FieldState) -> None:
        if self._options.get(key) != field.options:
            row.set_model(Gtk.StringList.new([label for _value, label in field.options]))
            self._options[key] = field.options
        values = [value for value, _label in field.options]
        if field.value in values:
            row.set_selected(values.index(field.value))

    # -- widgets -> actions -----------------------------------------------------------------

    def _combo(self, key: str, title: str) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, use_markup=False)

        def selected(_row: Adw.ComboRow, _pspec: object) -> None:
            options = self._options.get(key, ())
            index = row.get_selected()
            if not self._syncing and 0 <= index < len(options):
                self._actions.edit(key, options[index][0])

        row.connect("notify::selected", selected)
        return row

    def _entry(self, key: str, title: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)

        def changed(_row: Adw.EntryRow) -> None:
            if not self._syncing:
                self._actions.edit(key, row.get_text())

        row.connect("changed", changed)
        return row

    def _choose_folder(self) -> None:
        chooser = Gtk.FileDialog(title="Meetings folder", modal=True)
        current = self._state.field(MEETINGS_DIR).value if self._state.fields else ""
        if current:
            chooser.set_initial_folder(Gio.File.new_for_path(current))

        def chosen(source: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return  # dismissed: nothing changes
            path = folder.get_path() if folder is not None else None
            if path:
                self._actions.edit(MEETINGS_DIR, path)

        chooser.select_folder(self._parent_window, None, chosen)

    @staticmethod
    def _group(title: str | None, *children: Gtk.Widget) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(title)
        for child in children:
            group.add(child)
        return group


def _stale_text(field: FieldState) -> str:
    return (
        "Changed elsewhere since you edited this; not yet saved against the new value."
        if field.stale
        else ""
    )
