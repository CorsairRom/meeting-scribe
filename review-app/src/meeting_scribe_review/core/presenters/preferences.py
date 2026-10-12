"""The Preferences dialog's state: staged edits, groups that follow the provider, one atomic Save.

Nothing is saved while the user edits: ``save`` sends every changed field in one ``Set`` and the
service answers per field. The cloud API key is not a setting: this presenter only asks the keyring
port whether it exists (never for its value) and never holds one (BR-005). Labels and wording live
here, not in the service.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from meeting_scribe_review.core.errors import (
    KEYRING_UNAVAILABLE,
    RESTART_NOT_DONE,
    ServiceError,
    message_for,
)
from meeting_scribe_review.core.gateway import ApiKeyStore, SettingsGateway
from meeting_scribe_review.core.models import (
    KeyStatus,
    ModelList,
    SetOutcome,
    SettingsField,
)
from meeting_scribe_review.core.result import Err, Ok, Result

PROVIDER = "summaries.provider"
LOCAL_MODEL = "summaries.local_model"
LANGUAGE = "summaries.language"
CLOUD_MODEL = "summaries.cloud_model"
CLOUD_PROVIDER = "summaries.cloud_provider"
MEETINGS_DIR = "storage.meetings_dir"

LABELS = {
    PROVIDER: "Run summaries",
    CLOUD_PROVIDER: "Provider",
    LOCAL_MODEL: "Model",
    LANGUAGE: "Summary language",
    CLOUD_MODEL: "Model",
    MEETINGS_DIR: "Meetings folder",
}
LOCAL = "local"
CLOUD = "cloud"
PROVIDER_OPTIONS = ((LOCAL, "On this computer"), (CLOUD, "In the cloud"))
CLOUD_PROVIDER_OPTIONS = (("opencode-go", "OpenCode Go"),)
MODEL_SCOPES = {LOCAL_MODEL: LOCAL, CLOUD_MODEL: CLOUD}
UNSUPPORTED_TEXT = "Not supported yet"
_UNAVAILABLE = "SUMMARY_MODELS_UNAVAILABLE"
KEY_NOTES = {
    KeyStatus.UNKNOWN: "Checking the keyring...",
    KeyStatus.MISSING: "Set the API key first.",
    KeyStatus.LOCKED: "Unlock the keyring to load the models.",
    KeyStatus.UNAVAILABLE: "The keyring is not reachable.",
}
LIST_NOTES = {
    LOCAL: {
        _UNAVAILABLE: "Ollama is not reachable. The saved model is kept.",
    },
    CLOUD: {
        "SUMMARY_CLOUD_KEY_MISSING": KEY_NOTES[KeyStatus.MISSING],
        "SUMMARY_CLOUD_KEY_LOCKED": KEY_NOTES[KeyStatus.LOCKED],
        "SUMMARY_CLOUD_KEY_UNAVAILABLE": KEY_NOTES[KeyStatus.UNAVAILABLE],
        "SUMMARY_AUTH_FAILED": "The provider rejected the API key. Set it again.",
        _UNAVAILABLE: "The model list could not be read. Check the connection. "
        "The saved model is kept.",
    },
}
LOADING_NOTE = "Loading the models..."
EMPTY_NOTES = {LOCAL: "No models are installed in Ollama.", CLOUD: "The provider lists no models."}
NOT_LISTED_NOTE = "The saved model is not in the provider's list. Pick one from the list."
NOT_SUPPORTED_NOTE = "The saved model is not supported yet. Pick another one."
AUTO = "auto"
LANGUAGE_OPTIONS = (
    (AUTO, "Meeting language"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
    ("ca", "Catalan"),
    ("pl", "Polish"),
    ("ja", "Japanese"),
    ("zh", "Chinese"),
)
CLOUD_NOTE = (
    "Your transcript is sent to the cloud only when you press Generate summary, "
    "and you are asked every time."
)
RESTART_TEXTS = {MEETINGS_DIR: "Restart the service to use the new folder."}
RESTART_FALLBACK = "Restart the service to apply your changes."
RESTARTING_TEXT = "Restarting the service..."
RESTART_WAIT_SECONDS = 20.0
"""How long a restart may take before the dialog stops waiting for it."""
FOLDER_NOTE = "Existing meetings stay where they are."
KEY_TEXTS = {
    KeyStatus.UNKNOWN: "Checking the keyring...",
    KeyStatus.STORED: "Key stored",
    KeyStatus.MISSING: "No key stored",
    KeyStatus.LOCKED: "Keyring locked",
    KeyStatus.UNAVAILABLE: "Keyring unavailable",
}
_LEGACY = "LEGACY_SETTING"
_FIELD_INVALID = "FIELD_INVALID"
_APPLY_FAILED = "SETTINGS_APPLY_FAILED"
_REMOVE_FAILED = "KEYRING_REMOVE_FAILED"


@dataclass(frozen=True, slots=True)
class _ModelView:
    options: tuple[tuple[str, str], ...]
    selectable: bool
    note: str
    unsupported: tuple[str, ...] = ()
    unsupported_text: str = ""


class PreferencesPhase(StrEnum):
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"  # the service does not answer: read-only, Save disabled


@dataclass(frozen=True, slots=True)
class FieldState:
    key: str
    label: str
    kind: str
    value: str  # what the row shows: the staged text, else the saved value
    options: tuple[tuple[str, str], ...]  # (value, label) of a choice; empty otherwise
    visible: bool
    locked: bool
    lock_text: str
    error: str
    edited: bool
    stale: bool  # edited, and the saved value changed elsewhere since: "not saved against it"
    # A model field is a dropdown of ``options``; it can be used only when ``selectable``, and the
    # models this version cannot use are ``unsupported`` (shown, never selectable).
    selectable: bool = True
    note: str = ""  # why the list is missing, or a warning about the saved model
    unsupported: tuple[str, ...] = ()
    unsupported_text: str = ""


@dataclass(frozen=True, slots=True)
class PreferencesState:
    phase: PreferencesPhase = PreferencesPhase.LOADING
    fields: tuple[FieldState, ...] = ()
    message: str = ""
    hint: str = ""
    show_cloud: bool = False
    cloud_note: str = ""
    folder_note: str = FOLDER_NOTE
    key_status: KeyStatus = KeyStatus.UNKNOWN
    key_text: str = KEY_TEXTS[KeyStatus.UNKNOWN]
    can_remove_key: bool = False
    restart_text: str = ""
    can_restart: bool = False
    restarting: bool = False
    can_save: bool = False
    saving: bool = False
    saved: bool = False  # the save is complete and nothing needs the dialog any more: close it

    def field(self, key: str) -> FieldState:
        return next(f for f in self.fields if f.key == key)


def _options(row: SettingsField) -> tuple[tuple[str, str], ...]:
    if row.key == PROVIDER:
        return PROVIDER_OPTIONS
    if row.key == CLOUD_PROVIDER:
        known = {value for value, _ in CLOUD_PROVIDER_OPTIONS}
        extra = () if row.value in known else ((row.value, row.value),)
        return (*CLOUD_PROVIDER_OPTIONS, *extra)
    if row.key == LANGUAGE:
        known = {value for value, _ in LANGUAGE_OPTIONS}
        extra = () if row.value in known else ((row.value, row.value),)
        return (*LANGUAGE_OPTIONS, *extra)
    return ()


def _lock_text(row: SettingsField) -> str:
    if not row.locked:
        return ""
    if row.lock_reason == _LEGACY:
        return (
            "Held at Local by the obsolete SUMMARIES_CLOUD_ENABLED setting. Delete it from the "
            "environment and .env, then choose Cloud here."
        )
    if row.source == "dotenv":
        return "Set in .env (developer override). Remove the line to change it here."
    return "Set by the environment (developer override). Remove it to change it here."


class PreferencesPresenter:
    def __init__(
        self,
        gateway: SettingsGateway,
        keys: ApiKeyStore,
        on_change: Callable[[PreferencesState], None],
        after: Callable[[float, Callable[[], None]], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._keys = keys
        self._on_change = on_change
        self._rows: list[SettingsField] = []
        self._staged: dict[str, str] = {}
        self._stale: set[str] = set()
        self._errors: dict[str, str] = {}
        self._pending: tuple[str, ...] = ()
        self._phase = PreferencesPhase.LOADING
        self._text = ""
        self._hint = ""
        self._after = after or (lambda _seconds, _call: None)  # a timer: the view supplies one
        self._restarting = False
        self._went_down = False  # the service left the bus while a restart was awaited
        self._restart_token = 0
        self._save_done: bool | None = None  # a saved Set waits for the next fresh reload
        self._saving = False
        self._saved = False
        self._key = KeyStatus.UNKNOWN
        self._lists: dict[str, ModelList] = {}  # the model list of each scope, once it answered
        self._asked: set[str] = set()  # scopes whose list was requested (an answer may be due)
        self._list_generation: dict[str, int] = {}
        self._generation = 0
        self._state = PreferencesState()

    @property
    def state(self) -> PreferencesState:
        return self._state

    # -- loading ----------------------------------------------------------------------------

    def load(self) -> None:
        self._lists.clear()
        self._asked.clear()
        self._phase = PreferencesPhase.LOADING
        self._text = self._hint = ""
        self._publish()
        self._fetch()
        self.refresh_key()

    def _fetch(self) -> None:
        self._generation += 1
        generation = self._generation
        self._gateway.get_all(lambda result: self._loaded(generation, result))

    def _loaded(self, generation: int, result: Result[list[SettingsField], ServiceError]) -> None:
        if generation != self._generation:
            return  # a newer load answers
        save_done, self._save_done = (
            self._save_done,
            None,
        )  # whichever reload is newest completes it
        if isinstance(result, Err):
            self._phase = PreferencesPhase.UNAVAILABLE
            self._show_error(result.error)
            self._publish()
            return
        self._adopt(result.value)
        self._phase = PreferencesPhase.READY
        self._saved = bool(save_done)
        self._gateway.restart_required(self._pending_known)
        self._publish()
        self._ensure_models()

    def _adopt(self, rows: list[SettingsField]) -> None:
        old = {row.key: row.value for row in self._rows}
        self._rows = rows
        by_key = {row.key: row for row in rows}
        for key in list(self._staged):
            row = by_key.get(key)
            if row is None or row.locked or self._staged[key] == row.value:
                del self._staged[key]
            elif key in old and old[key] != row.value:
                self._stale.add(key)
        self._stale &= set(self._staged)

    def _pending_known(self, result: Result[tuple[str, ...], ServiceError]) -> None:
        if isinstance(result, Ok):
            self._pending = result.value
            self._publish()

    def on_service_available(self, available: bool) -> None:
        if available:
            self._restarting = False  # the service is back: whatever restarted it is done
            self._went_down = False
            self.load()
        elif self._restarting:
            self._went_down = True  # expected: it comes back, or the wait below gives up
        else:
            self._phase = PreferencesPhase.UNAVAILABLE
            self._show_error(ServiceError("SERVICE_NOT_RUNNING"))
            self._publish()

    def on_settings_changed(self, keys: tuple[str, ...]) -> None:
        del keys  # the whole registry is small: reload it and keep what the user typed
        if self._phase is PreferencesPhase.READY and not self._saving:
            self._fetch()

    # -- editing and saving -----------------------------------------------------------------

    def edit(self, key: str, value: str) -> None:
        row = self._row(key)
        if row is None or row.locked or self._phase is not PreferencesPhase.READY:
            return
        if self._is_unsupported(key, value):
            return  # an unsupported model is shown, never selectable
        if value == row.value:
            self._staged.pop(key, None)
        else:
            self._staged[key] = value
        self._errors.pop(key, None)
        self._stale.discard(key)
        self._saved = False
        self._publish()
        if key == PROVIDER:
            self._ensure_models()

    def save(self) -> None:
        if not self._state.can_save:
            return
        values = dict(self._staged)
        self._saving = True
        self._saved = False
        self._errors.clear()
        self._text = self._hint = ""
        self._publish()
        self._gateway.set_values(values, self._set_done)

    def _set_done(self, result: Result[SetOutcome, ServiceError]) -> None:
        self._saving = False
        if isinstance(result, Err):
            self._show_error(result.error)
            self._publish()
            return
        outcome = result.value
        if outcome.errors:
            for issue in outcome.errors:
                text = issue.message if issue.code == _FIELD_INVALID and issue.message else ""
                self._errors[issue.key] = text or message_for(ServiceError(issue.code)).text
            self._publish()
            return
        self._staged.clear()
        self._stale.clear()
        self._pending = outcome.restart_required
        needs_restart_now = bool(set(outcome.applied) & set(outcome.restart_required))
        # The service reports a failed apply in every row (``apply_problem``): the reload shows it.
        self._save_done = not outcome.apply_failed and not needs_restart_now
        self._fetch()

    # -- restarting -------------------------------------------------------------------------

    def restart(self) -> None:
        if not self._state.can_restart:
            return
        self._gateway.restart(self._restart_done)

    def _restart_done(self, result: Result[str, ServiceError]) -> None:
        if isinstance(result, Err):
            self._show_error(result.error)
        else:
            self._restarting = True
            self._went_down = False
            self._text = self._hint = ""
            self._restart_token += 1
            token = self._restart_token
            self._after(RESTART_WAIT_SECONDS, lambda: self._restart_timed_out(token))
        self._publish()

    def _restart_timed_out(self, token: int) -> None:
        """The restart was accepted, but the service never left or never came back (only logged)."""
        if token != self._restart_token or not self._restarting:
            return
        self._restarting = False
        if self._went_down:
            self._phase = PreferencesPhase.UNAVAILABLE
        self._show_error(ServiceError(RESTART_NOT_DONE))
        self._publish()

    # -- the API key row ---------------------------------------------------------------------

    def refresh_key(self) -> None:
        self._keys.is_stored(self._key_known)

    def _key_known(self, result: Result[KeyStatus, ServiceError]) -> None:
        self._key = result.value if isinstance(result, Ok) else KeyStatus.UNAVAILABLE
        # The cloud list depends on the key (a new key may be a valid one): ask again.
        self._lists.pop(CLOUD, None)
        self._asked.discard(CLOUD)
        self._list_generation[CLOUD] = self._list_generation.get(CLOUD, 0) + 1
        self._publish()
        self._ensure_models()

    # -- the model lists --------------------------------------------------------------------

    def refresh_models(self) -> None:
        """Ask the service for the list of the shown group again (Ollama may have a new model)."""
        scope = self._shown_scope()
        self._lists.pop(scope, None)
        self._asked.discard(scope)
        self._publish()
        self._ensure_models()

    def _shown_scope(self) -> str:
        row = self._row(PROVIDER)
        return self._staged.get(PROVIDER) or (row.value if row else LOCAL)

    def _ensure_models(self) -> None:
        """Ask for the list of the shown group once; the cloud list needs a stored key."""
        scope = self._shown_scope()
        if self._phase is not PreferencesPhase.READY or scope in self._asked:
            return
        if scope == CLOUD and self._key is not KeyStatus.STORED:
            return
        self._asked.add(scope)
        generation = self._list_generation.get(scope, 0) + 1
        self._list_generation[scope] = generation
        self._gateway.list_models(scope, lambda r: self._models_known(scope, generation, r))

    def _models_known(
        self, scope: str, generation: int, result: Result[ModelList, ServiceError]
    ) -> None:
        if generation != self._list_generation.get(scope):
            return  # a newer ask (or a key change) answers
        self._lists[scope] = result.value if isinstance(result, Ok) else ModelList(_UNAVAILABLE)
        self._publish()

    def _is_unsupported(self, key: str, value: str) -> bool:
        listing = self._lists.get(MODEL_SCOPES.get(key, ""))
        return listing is not None and any(
            m.id == value and not m.supported for m in listing.models
        )

    def remove_key(self) -> None:
        if self._state.can_remove_key:
            self._keys.remove(self._key_removed)

    def _key_removed(self, result: Result[None, ServiceError]) -> None:
        if isinstance(result, Err):
            code = result.error.code
            unreachable = code == KEYRING_UNAVAILABLE
            self._show_error(result.error if unreachable else ServiceError(_REMOVE_FAILED))
            self._publish()
            return
        self.refresh_key()

    # -- state ------------------------------------------------------------------------------

    def _row(self, key: str) -> SettingsField | None:
        return next((row for row in self._rows if row.key == key), None)

    def _show_error(self, error: ServiceError) -> None:
        message = message_for(error)
        self._text, self._hint = message.text, message.hint

    def _publish(self) -> None:
        self._state = self._build()
        self._on_change(self._state)

    def _build(self) -> PreferencesState:
        ready = self._phase is PreferencesPhase.READY
        provider = self._staged.get(PROVIDER) or (r.value if (r := self._row(PROVIDER)) else LOCAL)
        cloud = provider == CLOUD
        fields = tuple(self._field(row, cloud) for row in self._rows)
        edited = bool(self._staged)
        idle = ready and not self._saving and not self._restarting
        text, hint = self._text, self._hint
        if not text and self._rows:
            text = self._standing_problem()
        if self._restarting:
            text, hint = RESTARTING_TEXT, ""
        restart_key = next(iter(self._pending), "")
        return PreferencesState(
            phase=self._phase,
            fields=fields,
            message=text,
            hint=hint,
            show_cloud=cloud,
            cloud_note=CLOUD_NOTE if cloud else "",
            key_status=self._key,
            key_text=KEY_TEXTS[self._key],
            can_remove_key=self._key in {KeyStatus.STORED, KeyStatus.LOCKED} and idle,
            restart_text=RESTART_TEXTS.get(restart_key, RESTART_FALLBACK) if self._pending else "",
            can_restart=bool(self._pending) and idle,
            restarting=self._restarting,
            can_save=idle and edited,
            saving=self._saving,
            saved=self._saved,
        )

    def _standing_problem(self) -> str:
        """A problem the service still reports: an unreadable saved file, or a failed apply."""
        if any(row.apply_problem for row in self._rows):
            return message_for(ServiceError(_APPLY_FAILED)).text
        if any(row.file_problem for row in self._rows):
            return message_for(ServiceError("SETTINGS_FILE_INVALID")).text
        return ""

    def _model_view(self, scope: str, value: str) -> _ModelView:
        """The dropdown of a model field: its options, whether it can be used, and one note."""
        saved = ((value, value),)
        if scope == CLOUD and self._key is not KeyStatus.STORED:
            return _ModelView(saved, selectable=False, note=KEY_NOTES[self._key])
        listing = self._lists.get(scope)
        if listing is None:
            return _ModelView(saved, selectable=False, note=LOADING_NOTE)
        if not listing.available:
            note = LIST_NOTES[scope].get(listing.status, LIST_NOTES[scope][_UNAVAILABLE])
            return _ModelView(saved, selectable=False, note=note)
        supported = tuple(m.id for m in listing.models if m.supported)
        unsupported = tuple(m.id for m in listing.models if not m.supported)
        options = tuple((m, m) for m in supported)
        if value not in supported:
            options = (*saved, *options)  # the saved model stays selected, with a warning
        note = ""
        if not listing.models:
            note = EMPTY_NOTES[scope]
        elif value in unsupported:
            note = NOT_SUPPORTED_NOTE
        elif value not in supported:
            note = NOT_LISTED_NOTE
        return _ModelView(
            options,
            selectable=bool(supported),
            note=note,
            unsupported=unsupported,
            unsupported_text=UNSUPPORTED_TEXT if unsupported else "",
        )

    def _field(self, row: SettingsField, cloud: bool) -> FieldState:
        visible = not (row.key == LOCAL_MODEL and cloud) and not (
            row.key in {CLOUD_MODEL, CLOUD_PROVIDER} and not cloud
        )
        value = self._staged.get(row.key, row.value)
        model = self._model_view(MODEL_SCOPES[row.key], value) if row.key in MODEL_SCOPES else None
        return FieldState(
            key=row.key,
            label=LABELS.get(row.key, row.key),
            kind=row.kind,
            value=value,
            visible=visible,
            locked=row.locked,
            lock_text=_lock_text(row),
            error=self._errors.get(row.key, ""),
            edited=row.key in self._staged,
            stale=row.key in self._stale,
            options=model.options if model else _options(replace(row, value=value)),
            selectable=model.selectable if model else True,
            note=model.note if model else "",
            unsupported=model.unsupported if model else (),
            unsupported_text=model.unsupported_text if model else "",
        )
