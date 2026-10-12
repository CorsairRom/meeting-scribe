---
type: spec
feature: preferences
status: in-progress  # draft | approved | in-progress | done | blocked
mvp: true
prd_ref: NFR-1, NFR-6, safety requirements 2-3
depends_on:
  - decisions/001-thin-gnome-extension-dbus-service.md (thin clients over the D-Bus session bus, no HTTP)
  - decisions/005-review-ui-native-gtk-app.md (the GTK4 + libadwaita app on system Python, presenters without gi)
  - decisions/009-cloud-summaries-through-opencode-go.md (the API key lives in the GNOME Keyring)
  - specs/summaries.md (SummariesSettings, the cloud gate, product decisions 8 and 9)
  - specs/review-app.md (the app that hosts the window, launch and install layout)
  - architecture/api-contracts.md (Recording1, Meetings1, Summaries1; Settings1 is added to it)
verifiable_by:
  - tests/unit/preferences/
  - tests/unit/test_settings_overrides.py
  - tests/e2e/preferences/test_settings_over_dbus.py
  - tests/unit/review_app/test_preferences_presenter.py
  - tests/unit/test_install_scripts.py
  - gnome-extension/tests/
last_updated: 2026-10-12
ai_instructions:
  - "This spec is a contract. Do NOT implement anything that is not in it."
  - "If you need to change the spec, update this doc FIRST and ask for confirmation."
  - "Every acceptance criterion must have a test that verifies it."
  - "The API key never crosses D-Bus and is never a setting (BR-005). The Preferences window stores it through the existing app-side libsecret path."
  - "Tests never touch the real config directory, the real keyring, the real meetings folder or a real audio device; they use tmp_path, a fake key store and a private bus."
  - "Setting values are private (they can hold a home path): log keys, never values."
---

# Spec: Preferences (configure the service without a console)

## Goal (one sentence)

The user changes the few settings that matter (summary provider and models, summary language, cloud model and API key,
meetings folder) in a native Preferences window opened from the extension (the gear in the Extensions app and a
"Preferences..." menu item), and never edits `.env` or opens a terminal.

---

## Users / Personas

| Persona | How they use the feature |
|---|---|
| The user (single user, GNOME) | Installs with one command, opens Preferences from the top-bar menu, picks Local or Cloud, pastes the API key once, optionally changes the folder, and keeps working |
| The developer | Keeps overriding anything through environment variables or `.env`; the window shows which fields those override and locks them |

---

## User stories

- **US-1**: As the user, I want a Preferences window from the extension menu, so that I find settings where I find the recorder.
- **US-2**: As the user, I want the same window from the gear of the extension in the Extensions app, so that GNOME's own entry point works.
- **US-3**: As the user, I want to choose Local or Cloud and see only the fields that matter for my choice, so that the window stays small.
- **US-4**: As the user, I want choosing Cloud to be the whole opt-in, with a clear note that each request still asks me, so that I am not asked twice for the same decision.
- **US-5**: As the user, I want to set or replace the API key without a console and to see that one is stored, without the key ever being shown back.
- **US-6**: As the user, I want invalid input explained next to the field and nothing half-saved.
- **US-7**: As the user, I want to know which changes apply at once and which need a service restart, and to restart from the window when it is safe.
- **US-8**: As the developer, I want environment variables and `.env` to keep working as an override, and to see which fields they lock.

---

## Acceptance criteria (Gherkin to tests)

Test paths are planned locations; the task that builds each one is in [Task breakdown](#13-task-breakdown). Every criterion
runs against `tmp_path`, a fake key store, a fake systemd and a private bus. No test uses the real config directory,
keyring, meetings folder, network or audio device.

### AC-1: The window shows every exposed field with its current value and where it comes from

```gherkin
GIVEN a service with no settings file and no overriding environment
WHEN a client calls Settings1.GetAll
THEN it gets one row per exposed field with key, kind, value, default, source "default", locked false, apply "live" or "restart"
GIVEN SUMMARIES_LOCAL_MODEL is set in the process environment
THEN that row has source "env" and locked true, and the window shows the field read-only with the reason
GIVEN a value saved earlier in the settings file
THEN that row has source "file" and locked false
```

**Test**: `tests/unit/preferences/test_get_settings.py`, `tests/e2e/preferences/test_settings_over_dbus.py::test_get_all`

### AC-2: Saving validates everything first and applies nothing on any error

```gherkin
GIVEN valid and invalid values submitted together
WHEN a client calls Settings1.Set
THEN the reply lists a (key, code, message) row per invalid field, "applied" is empty, the file is unchanged and no signal is emitted
GIVEN only valid values
THEN they are written atomically, applied, "applied" lists the keys and SettingsChanged(keys) is emitted after the file is saved
GIVEN a key that is not exposed, or a locked key
THEN the row's code is FIELD_UNKNOWN or FIELD_LOCKED and nothing is applied
```

**Test**: `tests/unit/preferences/test_update_settings.py`, `tests/e2e/preferences/test_settings_over_dbus.py::test_set_all_or_nothing`

### AC-3: Validation reuses the settings classes and adds no second rule set

```gherkin
GIVEN the candidate values merged over the current ones
WHEN they are validated
THEN the same SummariesSettings and StorageSettings validators run (language is "auto" or a lowercase ISO code, models are not empty, provider is local or cloud)
AND an invalid candidate is reported per field with the validator's message, not as a raised exception
AND the service never starts from a state its own validators would reject
```

**Test**: `tests/unit/test_settings_overrides.py`

### AC-4: Choosing Cloud is the opt-in and there is no separate switch

```gherkin
GIVEN provider "local"
THEN Providers marks the cloud row unavailable with SUMMARY_CLOUD_DISABLED and Summarize(meeting, "cloud", true) fails with that code
WHEN the user saves provider "cloud"
THEN the cloud row is available (subject to the key), the default provider is cloud and no other setting is required
AND no field named "cloud enabled" exists in Settings1, in the window or in the settings file
AND Summarize still fails with SUMMARY_CLOUD_NOT_CONFIRMED unless cloud_confirmed is true
AND the button label and the confirmation dialog keep the service-provided "cloud" label
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_cloud_gates_follow_the_provider`, `tests/unit/preferences/test_cloud_derivation.py`

### AC-5: Cloud fields appear only when Cloud is selected

```gherkin
GIVEN Provider = Local
THEN the Cloud group (model, API key) is not shown and the Local group (model) is
WHEN the user selects Cloud
THEN the Cloud group appears with the transcript-leaves-the-computer note and "you will be asked before every request"
AND nothing is saved until the user presses Save (or the dialog's apply action)
```

**Test**: `tests/unit/review_app/test_preferences_presenter.py::test_groups_follow_the_provider`

### AC-6: Live settings take effect without a restart; a running summary keeps its snapshot

```gherkin
GIVEN a summary is running with the local model A
WHEN the user saves local model B
THEN the running summary finishes with model A
AND the next Summarize uses model B and Providers shows model B at once
AND no service restart happened
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_config_swap_keeps_the_running_job`

### AC-7: The meetings folder needs a restart, and the window says so

```gherkin
GIVEN a new meetings folder that is valid
WHEN it is saved
THEN it is stored, "restart_required" lists "storage.meetings_dir" and the window shows "Restart the service to use the new folder"
AND meetings in the old folder are not moved, copied or deleted
GIVEN a path that is relative, a file, not creatable, or not writable
THEN the field is rejected with FIELD_INVALID or FIELD_FOLDER_NOT_WRITABLE and nothing is saved
```

**Test**: `tests/unit/preferences/test_meetings_dir_field.py`

### AC-8: Restart is offered only when it is safe

```gherkin
GIVEN a recording, a processing job or a summary is active
WHEN a client calls Settings1.Restart
THEN it fails with SETTINGS_RESTART_BUSY and the service keeps running
GIVEN everything is idle
THEN it returns "RESTARTING", asks systemd to restart meeting-scribe.service after the reply is sent, and unfinished jobs resume as on any start
GIVEN systemd cannot be reached
THEN it fails with SETTINGS_RESTART_UNAVAILABLE and the window shows the manual command
```

**Test**: `tests/unit/preferences/test_restart.py` (fake systemd), `tests/e2e/preferences/test_settings_over_dbus.py::test_restart_refused_while_busy`

### AC-9: The API key never crosses D-Bus and is never a setting

```gherkin
GIVEN the Preferences window
WHEN the user presses "Set API key..." and saves
THEN the key goes from the field to the app's libsecret store (the existing ApiKeyPresenter and SecretKeyStore) and nowhere else
AND no Settings1 member, settings-file key, log record, error message or signal carries it
AND the stored indicator comes from a keyring existence check that does not load the secret
AND "Remove key" deletes the item and the indicator turns off
```

**Test**: `tests/unit/review_app/test_preferences_presenter.py::test_key_never_leaves_the_store`, `tests/unit/preferences/test_no_secret_in_settings.py` (an exposed-key allow-list test plus caplog)

### AC-10: Precedence is fixed and visible

```gherkin
GIVEN the same key set in the process environment, in .env, in the settings file and as a default
THEN the effective value is the environment's, then .env's, then the file's, then the default
AND GetAll reports the winning source per key
GIVEN a key locked by env
WHEN the user wants to change it
THEN the window shows which source locks it (environment or .env) and that removing it unlocks the field
```

**Test**: `tests/unit/test_settings_overrides.py::test_precedence_and_source`

### AC-11: A damaged settings file never blocks the service

```gherkin
GIVEN a settings file that is not valid JSON, or has an unknown version
WHEN the service starts
THEN it starts with environment, .env and defaults, logs SETTINGS_FILE_INVALID (code only, no content), and reports the problem through GetAll
AND the next successful Set moves the bad file to settings.json.invalid and writes a new one
GIVEN a file with one invalid value
THEN only that key falls back to its default, the others are kept, and the key is logged without its value
```

**Test**: `tests/unit/preferences/test_settings_file.py`

### AC-12: The window opens from the menu, from the app and from the Extensions gear

```gherkin
WHEN the user chooses "Preferences..." in the extension menu
THEN the extension launches the review app with --preferences
AND the app presents its window with the Preferences dialog on top (a second launch activates the running instance)
WHEN the user presses the gear of the extension in the Extensions app
THEN prefs.js launches the same app with --preferences and closes its own window
AND when the launch fails, prefs.js shows one message with the manual command instead of an empty window
GIVEN the service is not running
THEN the dialog opens read-only with "The Meeting Scribe service is not running" and the start command
```

**Test**: `tests/unit/review_app/test_launch.py::test_preferences_option`, `gnome-extension/tests/indicatorModel.test.js` (menu item), `gnome-extension/tests/shellGlue.test.js` (prefs.js static check, GO-031), `tests/unit/test_install_scripts.py::test_prefs_is_packed`

### AC-13: Legacy `SUMMARIES_CLOUD_ENABLED` is migrated safely

```gherkin
GIVEN a provider "cloud" saved from the Preferences window (settings.json)
THEN it is the opt-in by itself and nothing is migrated
GIVEN SUMMARIES_PROVIDER=cloud in the environment or .env and SUMMARIES_CLOUD_ENABLED absent, unparseable or false
THEN the provider resolves to local, GetAll reports the field locked with the "legacy setting" reason, and one WARNING names SUMMARIES_CLOUD_ENABLED (never a value)
GIVEN SUMMARIES_CLOUD_ENABLED=false and a provider "cloud" saved in the window
THEN the provider also resolves to local (an explicit off wins)
GIVEN SUMMARIES_PROVIDER=cloud and SUMMARIES_CLOUD_ENABLED=true
THEN the cloud stays selected and one WARNING says the variable is obsolete
GIVEN SUMMARIES_CLOUD_ENABLED=true and the provider is local
THEN one WARNING says the variable is obsolete and no longer allows a per-request cloud summary
AND no code reads cloud_enabled any more
```

**Test**: `tests/unit/test_settings_overrides.py::test_legacy_cloud_enabled`

### AC-14: Settings values are private

```gherkin
WHEN settings are loaded, changed or rejected
THEN logs hold keys and error codes only, never values (a value can hold a home path)
AND the settings file is created with permissions 0600 in a 0700 directory
AND no meeting content, speaker name or key is ever stored in it
```

**Test**: `tests/unit/preferences/test_settings_file.py::test_permissions`, `tests/unit/preferences/test_update_settings.py::test_logs_keys_not_values`

### AC-15: The Preferences window is a thin client

```gherkin
THEN the app reaches settings only through Settings1 and the key only through libsecret
AND presenters live in core without gi, and ui holds the Adw widgets (same split as the review app)
AND the extension prefs.js imports no Shell module and contains no settings logic
```

**Test**: import-linter contracts, `gnome-extension/tests/shellGlue.test.js`

---

## What this is NOT (out of scope for this spec)

- Not a general editor for every setting: recording, processing, transcription and speakers tunables stay environment-only (the schema is extensible; section 3 lists what is exposed).
- Not a way to edit the cloud base URL, the context budgets, timeouts or the local base URL (security-sensitive or expert values stay environment-only).
- Not storing the API key anywhere but the GNOME Keyring; not reading it back to show it.
- Not moving, merging or deleting existing meetings when the folder changes.
- Not a per-extension GSettings schema: the service owns the configuration, so prefs.js needs no schema.
- Not multi-profile settings, import or export.

---

## Edge cases

| Case | Behavior |
|---|---|
| Service not running when the window opens | Read-only dialog with the reason and the start command; Save is disabled |
| Service restarts while the window is open | The gateway reconnects; the window reloads `GetAll` and discards no unsaved edit silently (it marks them as "not yet saved against the new values") |
| Two windows or a client changes a value | `SettingsChanged` makes the window reload unedited fields; an edited field keeps the user's text |
| Provider = Cloud, no key | Saving is allowed; the stored indicator reads "No key stored" and Generate shows `SUMMARY_CLOUD_KEY_MISSING` with its next step |
| Keyring locked | The indicator reads "Keyring locked"; storing may prompt (user-initiated), the service never prompts |
| Local model not pulled | Saving is allowed; the failure shows when a summary runs (`SUMMARY_MODEL_NOT_FOUND`) |
| Meetings folder on a removable disk that disappears | Validation runs at save and at start; at start the service fails to use the folder exactly as today and logs the key only |
| Language "fixed" with a code outside the list | Rejected unless it matches the ISO rule; the list is a convenience, not the validator |
| A save races a running summary | The summary keeps its snapshot (AC-6); only new requests see the change |
| Settings file edited by hand while running | Not watched; read at start and rewritten by the next `Set` (documented in the runbook) |
| `XDG_CONFIG_HOME` unset or relative | Default `~/.config`; the same resolution as the `.env` path |

---

## Technical design

### 1. Architecture

```
GNOME Extensions app (gear)            Extension menu ("Preferences...")
  prefs.js (gjs, separate process)       extension.js (Shell)
        \                                  /
         launch `meeting-scribe-review --preferences`
                        |
        review app (system Python, GTK4 + libadwaita)
          core: PreferencesPresenter, SettingsGateway port, parsers
          ui:   Adw.PreferencesDialog, DBusSettingsGateway, SecretKeyStore (libsecret)
                        |  D-Bus session bus: Settings1            |  libsecret (never D-Bus)
                        v                                          v
        meeting-scribe service                              GNOME Keyring
          preferences (new bounded context): fields, validation use cases, JSON store
          main.py: wires validators from the settings classes, live config holder
```

**Recommendation: the service is the single owner of configuration.** Clients (the app, `prefs.js`) never read the
file and never write `.env`; they call `Settings1`. This keeps validation in one place (the pydantic settings
classes), avoids a second schema in GSettings, and makes the extension and the app equally thin (ADR-001).

**New bounded context `preferences`** (`src/meeting_scribe/preferences/{domain,application,infrastructure}`), independent
of every other domain (CR-001): it holds `FieldSpec` values, the use cases and the file store, and receives the validator
as an injected callable built in the composition root, which is the only place allowed to know every settings class.
Add `meeting_scribe.preferences` to the domain independence contract and give it its own layered contract; the
`Settings1` adapter lives in `preferences/infrastructure/dbus/`.

### 2. Which fields are exposed

The registry (one `FieldSpec` per field; labels live in the app, not in the service):

| Key | Kind | Backing setting | Apply | Notes |
|---|---|---|---|---|
| `summaries.provider` | choice `local`, `cloud` | `SUMMARIES_PROVIDER` | live | Cloud = opt-in (AC-4) |
| `summaries.local_model` | text | `SUMMARIES_LOCAL_MODEL` | live | Not empty; optional picker from `LocalModels` (task P8) |
| `summaries.language` | choice `auto` or an ISO code | `SUMMARIES_LANGUAGE` | live | UI: "Meeting language" or a fixed language; curated list of common languages, validator stays the ISO rule |
| `summaries.cloud_model` | text | `SUMMARIES_CLOUD_MODEL` | live | Shown only for Cloud; not empty |
| `storage.meetings_dir` | path | `STORAGE_MEETINGS_DIR` | restart | Absolute, creatable, writable (section 6) |

The API key is not a field. Everything else stays in environment variables, as today. Adding a field later is one
`FieldSpec` plus its UI row and needs no D-Bus change (the schema is data, section 4).

### 3. Persistence and precedence

- **Where**: `$XDG_CONFIG_HOME/meeting-scribe/settings.json` (default `~/.config/meeting-scribe/settings.json`), next to the optional `.env`. The directory is created `0700`, the file `0600`.
- **Format**: JSON, `{"version": 1, "values": {"summaries.provider": "cloud", ...}}`. JSON because the standard library reads and writes it (TOML has no writer in the standard library and a hand-rolled writer is code to maintain); the user is not expected to edit it.
- **Sparse**: only values the user changed are stored, so a new default in a later release reaches everyone who never touched the field. A value equal to the default is removed from the file when it is saved.
- **Atomic writes**: write a temporary file in the same directory, `fsync`, then `os.replace` (the same pattern as `meeting.json`, ADR-004). A failed write returns `SETTINGS_STORAGE_FAILED` and changes nothing.
- **Precedence, highest first**: (1) process environment, (2) `.env` file (XDG, else `./.env` in development), (3) `settings.json`, (4) defaults.
  Environment and `.env` stay as a **developer override**: they win and the window shows the field locked with its source.
  The rule is one sentence in the runbook: "what you set in the window applies unless the environment says otherwise."
- **How it is built**: `Settings.load()` gains `with_overrides(values)`: the file values are passed as `init` input **below** the environment sources in `settings_customise_sources`, i.e. file values fill only what the environment and `.env` did not set. The source per key is computed once at load by checking which of the three layers supplies it.
- **Alternative rejected**: file above `.env`. It would make the window always effective, but a developer's `.env` would be silently ignored, which is the more surprising failure; the lock indicator costs one row of UI.
- **Invalid or damaged file**: see AC-11. The service never refuses to start because of the file.

### 4. D-Bus: `dev.meetingscribe.Settings1`

A new interface on the existing bus name and object path (same reasoning as `Summaries1`: old clients ignore it, `Meetings1`
does not grow). Implemented in `preferences/infrastructure/dbus/`. `api-contracts.md` is updated first, in the same
commit as the code.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `GetAll` | method | `() -> aa{sv}` | One row per exposed field: `key` (s), `kind` (s: `choice`, `text`, `path`), `value` (s), `default` (s), `source` (s: `default`, `file`, `env`, `dotenv`), `locked` (b), `lock_reason` (s, empty or a code such as `LEGACY_SETTING`), `apply` (s: `live`, `restart`), `choices` (as, empty unless `choice`). Values are strings; the app parses by `kind`. No secrets exist to return | none |
| `Set` | method | `(a{ss}) -> a{sv}` | Validate the whole candidate; on success write, apply and signal. Returns `applied` (as), `restart_required` (as, all keys pending restart, not only this call's), `errors` (a(sss): key, code, message). Atomic: any error means `applied` is empty | `SETTINGS_STORAGE_FAILED` |
| `Restart` | method | `() -> s` | Restart the service through systemd when idle; returns `RESTARTING` | `SETTINGS_RESTART_BUSY`; `SETTINGS_RESTART_UNAVAILABLE` |
| `LocalModels` | method | `() -> as` | Optional (task P8): model names installed in the local Ollama, read from its loopback `/api/tags`; no transcript data is involved | `SUMMARY_PROVIDER_UNAVAILABLE` |
| `RestartRequired` | property | `as` (read) | Keys saved but not yet in effect | n/a |
| `SettingsChanged` | signal | `(as)` | Keys written, emitted after the file is saved; `PropertiesChanged` carries `RestartRequired` | n/a |

Rules: one `Set` at a time (a lock; the second waits). Messages carry keys and codes, never values. Field error codes:
`FIELD_UNKNOWN`, `FIELD_LOCKED`, `FIELD_INVALID`, `FIELD_FOLDER_NOT_WRITABLE`. Clients treat `UnknownMethod` as "this service
is too old; update it".

### 5. Live versus restart, and restarting safely

- **Live (summaries)**: `build_summary_config` and the two providers read the settings at construction today. Change: `SummaryController` receives a `SummaryConfigSource` (a callable returning the current `SummaryConfig`), and the adapters that hold a model or base URL are rebuilt per swap by the composition root. A running job keeps the `SummaryConfig` it started with (AC-6). The swap is one reference assignment under the settings lock. No other controller changes.
- **Restart (meetings folder)**: `meetings_dir` is captured by the repository, recording and processing controllers at construction. Making it live would rebuild three controllers under load for a rarely changed value, so it applies at the next start. The window says so and offers "Restart now".
- **Can the service restart itself safely? Yes, through systemd, and only when idle.** `Restart` checks that no recording, job or summary is active (the controllers already expose these states), replies `RESTARTING`, then a short timer asks the user systemd manager (`org.freedesktop.systemd1.Manager.RestartUnit("meeting-scribe.service", "replace")` over `dbus-fast`, already a dependency). systemd stops it with `KillMode=mixed` and starts it again; `processing.resume()` already recovers unfinished jobs. No `os.execv` and no exit-code trick (the unit has `Restart=on-failure` and `RestartPreventExitStatus=1`, so exiting is not a restart). If the manager is unreachable the error carries the manual command.
- **When to prefer live**: any new field that only feeds `SummaryConfig` is live; a field that feeds a constructor of a long-lived controller is `restart` until someone pays for hot-swapping it.

### 6. Validation

- **One rule set**: the candidate values are applied over the current effective values and a full `SummariesSettings` and `StorageSettings` are built; a pydantic `ValidationError` is converted into per-field rows (key mapped from the error location). `Settings.with_overrides(values) -> Result[Settings, list[FieldProblem]]` is the only entry point (controllers return `Result`, never raise).
- **Cross-field**: the context-versus-output rule of `SummariesSettings` still runs (it concerns env-only fields, so a UI change cannot break it, but the check stays automatic).
- **`storage.meetings_dir`** (a check on the folder itself, so it lives beside the field in `preferences/infrastructure`): expand `~`, require an absolute path with no `..`, reject an existing non-directory, create the directory (mode `0700`) if its parent exists and is writable, then verify writability by creating and removing one temporary file with a fixed neutral name. The service never lists or reads the folder. Failure gives `FIELD_FOLDER_NOT_WRITABLE`.
- **Language**: `auto` or a lowercase ISO code, via the existing `SummariesSettings` rule.
- **Free-text models**: not empty, no whitespace-only values, a length bound; whether a model exists is learned when a request runs (as today).
- **Cloud**: choosing `cloud` needs no extra field; the base URL and `https` rule stay in `SummariesSettings` and are env-only.

### 7. Removing `SUMMARIES_CLOUD_ENABLED` and migration

- `SummariesSettings.cloud_enabled` is deleted; `SummaryConfig.cloud_enabled` becomes a property derived from `default_provider == "cloud"`. The gate order in `SummaryController.Summarize` is unchanged with the derived value (unknown provider, cloud not selected, not confirmed, then the rest, then the key), and `Providers` marks the cloud row unavailable with `SUMMARY_CLOUD_DISABLED` when the provider is local.
- **Rule for existing installs** (the cloud is opt-in; the migration reads the environment and `.env` once at load; nothing is rewritten): a provider `cloud` chosen in the Preferences window (`settings.json`) is the opt-in by itself. A provider `cloud` that comes from the environment or `.env` keeps working only next to an explicit `SUMMARIES_CLOUD_ENABLED=true`; with the variable absent, `false` or unparseable the provider resolves to `local` with one WARNING that names the variable (never a value), so an upgrade never switches the cloud on. An explicit `false` also overrides a provider saved in the window. The field is reported locked with `lock_reason` `LEGACY_SETTING` and the runbook says to delete the legacy lines and choose Cloud in the window to unlock it.
- **By design (OQ-8, one button)**: `SUMMARIES_CLOUD_ENABLED=true` with the provider `local` no longer allows a per-request cloud summary: the cloud is used only when the provider is `cloud`. Such a variable is ignored with one WARNING that says so.
- The owner's `.env` is not touched by tooling. `.env.example` drops the variable and says the window is the normal way to configure the summaries.
- The rule is removed one release after the window ships.

### 8. The Preferences window (review app)

- **Widget**: `Adw.PreferencesDialog` (libadwaita 1.5 and later; 1.9.3 is installed here, `PreferencesWindow` is deprecated). One page, groups:
  - **Summaries**: Provider (`Adw.ComboRow`: Local, Cloud); Summary language (`Adw.ComboRow`: "Meeting language" plus a fixed list).
  - **Local** (visible for Local): Model (`Adw.EntryRow`).
  - **Cloud: OpenCode Go** (visible for Cloud): Model (`Adw.EntryRow`); API key (`Adw.ActionRow` with a "Set API key..." button and a status suffix "Key stored" / "No key stored" / "Keyring locked", plus "Remove" when stored); a one-line note: "Your transcript is sent to the cloud only when you press Generate summary, and you are asked every time."
  - **Recordings**: Meetings folder (`Adw.ActionRow` showing the path with a "Choose..." button using `Gtk.FileDialog.select_folder`); a subtitle line "Existing meetings stay where they are" and, after a save that needs it, a banner "Restart the service to use the new folder" with "Restart now".
- **Save model**: edits are staged; an `Adw.Toast`/inline row error shows per-field problems from `Set`; the dialog applies on an explicit "Save" and closes on success. No auto-save per keystroke (cross-field validation and the file write are one transaction).
- **Core**: `PreferencesPresenter` (no `gi`) holds the staged values, which groups are visible, field errors, the key indicator and the restart banner; `SettingsGateway` port (`get_all`, `set`, `restart`, `changed` signal); `ApiKeyStore` gains `is_stored()` and `remove()`; the existing `ApiKeyPresenter` and the key dialog are reused for "Set API key...".
- **Key indicator**: libsecret search for the item by its attributes without requesting the secret (a locked collection still reports existence); it never calls `password_lookup`. This is an app-side check, so the key and its existence never touch the service or D-Bus.
- **Launch**: add `--preferences` to the app's command line (`launch.py` parses it like `--meeting`). `do_command_line` creates or reuses the window and presents the dialog over it. A second launch (for example the Extensions gear after the menu) activates the running instance. Presenting a dialog with no visible parent window was not verified; the design always has the review window as parent (assumption A-2).
- **Also reachable from the app**: a "Preferences" entry in the review window's primary menu (same dialog), so the window is not hidden behind the extension.

### 9. Extension integration and the GNOME 50 preferences model

**Verified** ([gjs.guide, extension preferences](https://gjs.guide/extensions/development/preferences.html), 2026-10-12;
the `gnome-extensions` manual via Context7): `prefs.js` default-exports a class extending `ExtensionPreferences` and
implements `fillPreferencesWindow(window)`, which receives an `Adw.PreferencesWindow`; it runs **in a separate process,
without access to GNOME Shell code** (no `Meta`, `Clutter`, `Shell`, `St`); `gnome-extensions pack` includes `prefs.js`
automatically when present; `gnome-extensions prefs <uuid>` opens it for testing. **Not stated in the docs**: whether
launching another application from `prefs.js` is acceptable. It is an ordinary gjs process running as the user, so
`Gio.DesktopAppInfo.launch` works technically; there is no review policy for local installs, but it is unusual and is
verified manually (assumption A-1).

**Recommendation: `prefs.js` launches the review app and closes.** It builds no settings UI and no D-Bus client, so there
is one Preferences window to build and test, not two. Design of the minimal `prefs.js` (`gnome-extension/meeting-scribe@meetingscribe.dev/prefs.js`):

- `fillPreferencesWindow(window)`: launch `dev.meetingscribe.Review.desktop` with `--preferences` (the desktop entry already installed by `make install`; same `Gio.DesktopAppInfo` approach as `launchReview` in `extension.js`, using `launch_uris` with the action or a command-line launch through `Gio.AppLaunchContext`), then `window.close()`.
- On failure (entry missing or launch error), add one `Adw.PreferencesPage` with an `Adw.StatusPage`: "Meeting Scribe Review is not installed" and the command `make install`, so the user never sees an empty window.
- **Fallback if launching from `prefs.js` is rejected in the manual check (A-1)**: prefs.js hosts only the **summary provider, models, language and meetings folder** through the same `Settings1` API using `Gio.DBusProxy` (the same descriptors as `meetingsClient.js`), and leaves the API key to the review app ("Set API key..." launches the app). That doubles the UI; it is the documented plan B and is not built unless needed.
- **Packaging**: `gnome-extensions pack` already includes `prefs.js`; the install script needs no new `--extra-source` (only `lib/` is extra today). `install.sh` and its test check that the packed zip contains `prefs.js`, and `shellGlue.test.js` checks it statically (GO-031: only `gi://` and `resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js` imports).
- **Menu item**: "Preferences..." is added to the extension menu in every state in which the menu shows (including service unavailable, where the dialog explains), as a new action id `preferences` handled like `review` (`launchReview` gains an argument list). It is always offered, not gated like "Review last meeting".
- **Process model note**: `prefs.js` and `extension.js` share nothing; both launch the same desktop entry, which is why a second launch must activate the running app (AC-12).

### 10. Errors and stable codes

Registered in `error-codes-catalog.md` and mapped by `to_dbus_error`; messages carry keys and next steps, never values.

| `error_code` | Meaning | Next step in the message |
|---|---|---|
| `SETTINGS_STORAGE_FAILED` | The settings file could not be written | Check disk space and permissions of the config folder |
| `SETTINGS_FILE_INVALID` | The settings file could not be read (logged at start; surfaced by `GetAll`) | The file is ignored; save again to replace it |
| `SETTINGS_RESTART_BUSY` | A recording, job or summary is active | Try again when it ends |
| `SETTINGS_RESTART_UNAVAILABLE` | systemd could not be reached | Run `systemctl --user restart meeting-scribe.service` |
| `FIELD_UNKNOWN` / `FIELD_LOCKED` / `FIELD_INVALID` / `FIELD_FOLDER_NOT_WRITABLE` | Per-field codes inside `Set` replies | Fix the field; for `FIELD_LOCKED`, remove the environment override |

### 11. Privacy

Setting values are not secrets, except the API key which is not a setting. They can still contain a home path, so logs
carry keys and codes only (AC-14). The file lives with the user's other configuration, `0600`. The window never displays
a stored key, and a cloud selection shows the "transcript leaves your computer" note beside the model field. The
per-request confirmation of safety requirement 3 is untouched.

### 12. Test strategy

| Level | What | How |
|---|---|---|
| Unit | Field registry, precedence and source, legacy migration, update use case, restart gate, file store (atomic, permissions, damaged file) | `tmp_path`, injected environment, fake systemd, fake clock |
| Unit | Summary controller gates and live swap | In-memory repository, fake providers (existing fixtures) |
| D-Bus e2e | Every `Settings1` member, error rows, signals | Private `dbus-daemon`, `dbus_fast` client; `XDG_CONFIG_HOME` pointed at `tmp_path` |
| App core | Presenter (groups follow the provider, staged edits, errors, key indicator, restart banner) | Fake gateway and fake key store; parsers fed real `Settings1` replies from the private bus |
| App views | Import smoke on system Python | Constructs no window |
| Install | `prefs.js` present in the packed zip | Fake `gnome-extensions` in the install tests |
| Extension | Menu item, `prefs.js` static check | `gjs -m` tests, `node --check` (GO-031) |
| Manual with consent | The gear and the menu open the window; Extensions gear launch and close; a real restart from the window | No audio, no recording, no cloud request |

---

### 13. Task breakdown

Advisory size: about 400 authored lines per work unit (not a cap). Order is the work-unit order; each unit ships its tests
and docs. Branch per phase from `main`, one commit per unit, no push without asking.

| Task | Content | Rough size |
|---|---|---|
| P1 | `preferences` domain and infrastructure: `FieldSpec` registry, `Settings.with_overrides`, precedence and `source`, JSON file store (atomic, permissions, damaged file), meetings-folder check, errors and catalog, import contracts | ~400 |
| P2 | Remove `cloud_enabled` (derived from the provider), legacy migration rule, `SummaryConfigSource` live swap in `main.py` and `SummaryController`, tests, `.env.example`, `ARCHITECTURE.md` section 7, `summaries.md` follow-up | ~350 |
| P3 | `Settings1` adapter and wiring, `GetAll`/`Set`/signal/property, restart use case with the systemd adapter, `api-contracts.md` first, e2e on a private bus | ~400 |
| P4 | App core: models, parsers, `SettingsGateway`, `PreferencesPresenter`, key `is_stored`/`remove`, error messages and drift test | ~400 |
| P5 | App `ui`: `Adw.PreferencesDialog`, D-Bus gateway, `--preferences` option, primary-menu entry, folder chooser, key row and restart banner | ~400 |
| P6 | Extension: `prefs.js` launcher with the failure page, "Preferences..." menu item, `gjs` and static tests, install test for the packed `prefs.js` | ~250 |
| P7 | Runbook `docs/runbooks/preferences.md`, rules-registry entries, `mvp-scope.md` and backlog, README, validation with the user's consent (open from the menu and the gear, change the provider and folder, restart), docs gate | ~150 plus docs |
| P8 (optional) | `LocalModels` (Ollama `/api/tags` on loopback) and a model picker in the Local group; ships only if the free-text row proves error-prone | ~250 |

---

## Library facts verified (2026-10-12)

| Fact | Source | Status |
|---|---|---|
| Extension preferences run in a separate process without Shell access; `prefs.js` exports an `ExtensionPreferences` subclass with `fillPreferencesWindow(window)`; Meta, Clutter, Shell and St are not available there | <https://gjs.guide/extensions/development/preferences.html> | Verified in docs |
| `gnome-extensions pack` includes `prefs.js` when present; `gnome-extensions prefs <uuid>` opens it | `gnome-extensions` manual via Context7 (`/git_gitlab_gnome_org/gnome_gnome-shell`); local `gnome-extensions pack` usage lists only `--extra-source` for additional files | Verified in docs |
| `Adw.PreferencesDialog`, `Adw.ComboRow`, `Adw.PasswordEntryRow` exist in libadwaita; `Adw.PreferencesWindow` is the older widget | gjs-docs via Context7 (`/websites/gjs-docs_gnome`) | Verified in docs |
| Installed: libadwaita 1.9.3, GTK 4.22.4, GNOME Shell 50.4, python3-gobject 3.56.3, libsecret 0.21.7 | `rpm -q` (read-only) | Verified on this machine |
| The service unit has `Restart=on-failure` and `RestartPreventExitStatus=1`; D-Bus activation delegates to `SystemdService=meeting-scribe.service` | `systemctl --user show`, `packaging/` (read-only) | Verified on this machine |

**Not verified**: that `prefs.js` may launch another application (the docs are silent); that `Adw.Dialog.present` works with no
visible parent window; that `Manager.RestartUnit` called from the unit's own process completes the restart cleanly (expected
from systemd's job model); the exact libsecret search flags that report a locked item without loading the secret.

---

## Assumptions to confirm

| ID | Assumption | Confirm in |
|---|---|---|
| A-1 | `prefs.js` can launch `dev.meetingscribe.Review.desktop` with `--preferences` and close its own window without a crash or an empty flash | P6, manual check by the owner (`gnome-extensions prefs meeting-scribe@meetingscribe.dev`); plan B in section 9 otherwise |
| A-2 | The Preferences dialog is always presented over the review window; the `--preferences` launch creates the window first | P5 |
| A-3 | A libsecret search without the load-secrets flag reports a locked item as existing | P4 with a fake store; P7 manual check |
| A-4 | `RestartUnit` from inside the unit restarts it cleanly and `processing.resume()` recovers any interrupted job | P3 (fake systemd), P7 manual check |
| A-5 | Rebuilding the summary providers per swap is cheap (no network at construction) | P2 (already true: nothing is sent until a request) |
| A-6 | The owner's existing `.env` may set `SUMMARIES_*` keys; those fields will appear locked until the lines are removed | P7 runbook; the file is never read by tooling |

---

## Product decisions

Decided by the owner on 2026-10-11:

1. **Entry points**: the gear of the extension in the Extensions app and a "Preferences..." item in the extension menu.
2. **Window**: Summaries (Provider Local or Cloud, Local model, Summary language, Cloud model and API key with a stored indicator) and Recordings (Meetings folder).
3. **No separate "Enable cloud" switch**: choosing Cloud IS the opt-in; cloud fields appear only for Cloud; the per-request confirmation stays. Recorded as decision 9 in `summaries.md`.

No product question is open. The technical choices above (service-owned settings file, environment as a developer
override, folder change needing a restart, `prefs.js` as a launcher) are recommendations that follow from those
decisions and from the existing architecture; they are listed in the assumptions where they rest on something not yet
verified.

---

## Definition of Done

- [ ] All acceptance criteria pass in `make check`
- [ ] `Settings1` is documented in `architecture/api-contracts.md` before the code (P3)
- [ ] New error codes registered in `architecture/error-codes-catalog.md`
- [ ] `SUMMARIES_CLOUD_ENABLED` removed from the code, `.env.example` and `ARCHITECTURE.md` section 7; the migration rule is tested
- [ ] New import contracts for `preferences` listed in `ARCHITECTURE.md`
- [ ] Unit tests written first (RED), then implementation (GREEN)
- [ ] Lint, format, type check and import contracts clean
- [ ] The key appears in no settings file, log, D-Bus reply or error (AC-9, AC-14)
- [ ] `prefs.js` is packed by `make install` and checked statically
- [ ] `docs/runbooks/preferences.md` written (precedence, locked fields, restart, the settings file)
- [ ] Manual validation with the user's consent: open from the menu and the gear, change settings, restart from the window
- [ ] Docs gate before the integration PR is reported ready (`project/doc-sync-protocol.md`)

---

## Decisions taken during implementation

> If unresolved forks appear, record them here AND open an ADR if the decision is structural.

- **Language field**: `summaries.language` is `kind` `choice` with `choices` `["auto"]` only; any lowercase ISO code is accepted by the validator, and the curated list lives in the app (P4/P5).
- **File problem in `GetAll`**: the spec says a damaged file is "reported through GetAll" without a member for it, so every row carries `file_problem` (empty or `SETTINGS_FILE_INVALID`).
- **`applied` and `SettingsChanged`** list the keys submitted in the successful `Set` (restart fields included); `restart_required` tells which of them are pending.
- **`Restart` is two steps** (`check`, reply, `perform` 0.3 s later) because a reply cannot follow a restart. Reachability is probed before the reply with `Manager.LoadUnit`; a failure after the reply is only logged. Verified in `man org.freedesktop.systemd1` (systemd 259).
- **Folder creation**: validation never creates the meetings folder; it is created (mode 0700) only after every field passed, just before the file is written. If applying a live change raises after the save, the `Set` reply carries `apply_failed` (saved, restart picks it up); an empty `Set` is a no-op.
- **Startup**: `run()` loads the file with `load_saved_settings` (damaged file or invalid key falls back, logged by key and code); `create_app()` without arguments still reads the environment only.
- **App core (P4)**: the gateway port is `SettingsGateway` with `get_all`, `set_values`, `restart_required` (read through `org.freedesktop.DBus.Properties.Get`, because the proxies do not load properties), `restart` and `watch_settings` (the `SettingsChanged` signal and the availability of the service). The app does not follow `PropertiesChanged`: every save emits `SettingsChanged`, which reloads the rows and the pending restart. `ApiKeyStore` gains `is_stored` (answers a `KeyStatus`, never the value) and `remove`. A successful save closes the dialog only when it needs nothing else: not when a restart-field was saved (the banner must stay) and not when `apply_failed`. Field problems show the service's message for `FIELD_INVALID` and the app's own wording for the other codes.
- **Apply failures persist (follow-up)**: `GetAll` rows carry `apply_problem` (empty or `SETTINGS_APPLY_FAILED`, the same on every row) until a later apply succeeds, so a client that missed the `Set` reply still learns it. A failed file write removes a meetings folder the same `Set` just created, only while it is still empty.
- **Preferences dialog (P5)**: the dialog is `Adw.PreferencesDialog` over `PreferencesState` (`ui/preferences_dialog.py`), opened by `PreferencesController` (one `PreferencesSession` per window, a dialog per opening). Per-field problems, lock reasons and "changed elsewhere" notes are lines in the description of the group that holds the field, because `Adw.EntryRow` has no subtitle. The restart banner is an `Adw.ActionRow` with a "Restart service" button (a banner widget has no slot in a dialog). Save is a button at the bottom of the page. `--preferences` and the primary-menu entry (`win.preferences`, Ctrl+comma) both call `ReviewWindow.open_preferences`, so the dialog always has the review window as parent (A-2). The key editor is the shared `ui/api_key_dialog.py`. No widget is constructed in the tests (no display); the dialog needs the manual check.
