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
from meeting_scribe_review.core.presenters.models import ModelsState
from meeting_scribe_review.core.presenters.preferences import (
    CLOUD_MODEL,
    CLOUD_PROVIDER,
    LANGUAGE,
    LOCAL_MODEL,
    MEETINGS_DIR,
    PROVIDER,
    FieldState,
    PreferencesPhase,
    PreferencesState,
)
from meeting_scribe_review.ui.models_group import ModelsActions, ModelsGroup

TITLE = "Preferences"
SAVE_LABEL = "Save"
SAVING_LABEL = "Saving..."


@dataclass(frozen=True, slots=True)
class PreferencesActions:
    edit: Callable[[str, str], None]
    save: Callable[[], None]
    restart: Callable[[], None]
    refresh_models: Callable[[], None]
    set_key: Callable[[], None]
    remove_key: Callable[[], None]
    models: ModelsActions


def _button(label: str, handler: Callable[[], None], *, css: str = "") -> Gtk.Button:
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if css:
        button.add_css_class(css)
    button.connect("clicked", lambda _button: handler())
    return button


def _icon_button(icon: str, tooltip: str, handler: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    button.add_css_class("flat")
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

        self._provider = self._combo(PROVIDER, "Run summaries")
        self._language = self._combo(LANGUAGE, "Summary language")
        self._cloud_provider = self._combo(CLOUD_PROVIDER, "Provider")
        # A model is picked, never typed. ``Adw.ComboRow`` cannot disable single items, so the
        # models this version cannot use are a greyed list under the dropdown.
        self._local = self._combo(LOCAL_MODEL, "Model")
        self._local.add_suffix(
            _icon_button("view-refresh-symbolic", "Refresh", actions.refresh_models)
        )
        self._cloud = self._combo(CLOUD_MODEL, "Model")
        self._unsupported = Adw.ExpanderRow(title="Not supported yet", use_markup=False)
        self._unsupported_rows: list[Adw.ActionRow] = []
        self._unsupported_shown: tuple[str, ...] = ()
        self._unsupported.set_visible(False)
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
        self._local_group = self._group("On this computer (Ollama)", self._local)
        self._cloud_group = self._group(
            "In the cloud", self._cloud_provider, self._key_row, self._cloud, self._unsupported
        )
        for group in (self._local_group, self._cloud_group):
            # In the header, not in the model row: it stays clickable while the dropdown is not.
            group.set_header_suffix(
                _icon_button(
                    "view-refresh-symbolic", "Refresh the model list", actions.refresh_models
                )
            )
        self._restart_group = self._group(None, self._restart_row)
        self._recordings = self._group("Recordings", self._folder)
        self._models = ModelsGroup(actions.models)  # acts at once: not part of Save
        page = Adw.PreferencesPage()
        for group in (
            self._notice,
            self._summaries,
            self._local_group,
            self._cloud_group,
            self._recordings,
            self._models,
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

    def update_models(self, state: ModelsState) -> None:
        self._models.update(state)

    def _sync_fields(self, state: PreferencesState, *, ready: bool) -> None:
        if not state.fields:
            return
        for key, row in (
            (PROVIDER, self._provider),
            (LANGUAGE, self._language),
            (CLOUD_PROVIDER, self._cloud_provider),
        ):
            field = state.field(key)
            self._sync_combo(key, row, field)
            row.set_sensitive(ready and not field.locked)
        for key, row in ((LOCAL_MODEL, self._local), (CLOUD_MODEL, self._cloud)):
            field = state.field(key)
            self._sync_combo(key, row, field)
            row.set_subtitle(field.note)  # why there is no list, or a warning about the saved model
            row.set_sensitive(ready and not field.locked and field.selectable)
        self._sync_unsupported(state.field(CLOUD_MODEL))
        folder = state.field(MEETINGS_DIR)
        self._folder.set_subtitle(folder.value)
        self._folder.set_sensitive(ready and not folder.locked)
        _describe(self._summaries, self._notes(state, PROVIDER, LANGUAGE))
        _describe(self._local_group, self._notes(state, LOCAL_MODEL))
        _describe(
            self._cloud_group, [state.cloud_note, *self._notes(state, CLOUD_PROVIDER, CLOUD_MODEL)]
        )
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

    def _sync_unsupported(self, field: FieldState) -> None:
        """The models this version cannot use: shown greyed under the dropdown, never selectable."""
        if self._unsupported_shown == field.unsupported:
            return
        for row in self._unsupported_rows:
            self._unsupported.remove(row)
        self._unsupported_rows = []
        for model_id in field.unsupported:
            row = Adw.ActionRow(title=model_id, subtitle=field.unsupported_text, use_markup=False)
            row.set_sensitive(False)
            self._unsupported.add_row(row)
            self._unsupported_rows.append(row)
        self._unsupported_shown = field.unsupported
        self._unsupported.set_visible(bool(field.unsupported))

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
