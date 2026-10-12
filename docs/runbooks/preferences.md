---
type: runbook
status: active
last_updated: 2026-10-12
phase: F5
verifiable_by:
  - docs/specs/preferences.md
  - src/meeting_scribe/preferences/domain/fields.py
  - scripts/install.sh
  - tests/unit/test_install_scripts.py
ai_instructions:
  - "An agent must NEVER run these steps against the owner's machine: the window opens the owner's settings and keyring. Only the user runs them."
  - "Never open ~/.config/meeting-scribe/, read settings.json or .env, or print an API key. Setting values can hold a home path."
---

# Change settings in Preferences

## Purpose

Change the few settings that matter (where summaries run, the cloud provider and its API key, the summary model, the
summary language, the meetings folder) in the Preferences window, without editing a file or opening a console.
Design: [`../specs/preferences.md`](../specs/preferences.md).

## Preconditions

- `make install` was run and the service is active: `systemctl --user status meeting-scribe.service`.
- For the Local model list, Ollama is running; for the Cloud model list, an API key is stored (see below).

## Steps

1. Open Preferences, either way:
   - the **Preferences...** item at the bottom of the Meeting Scribe menu in the top bar, or
   - the gear of **Meeting Scribe** in the Extensions app.

   Both launch the review app with the Preferences dialog on top. If the service is not running, the dialog opens
   read-only with "The Meeting Scribe service is not running" and the start command
   (`systemctl --user start meeting-scribe.service`).
2. Change the fields (table below). Nothing is saved until you press **Save**. Invalid input is explained next to
   the field and nothing is half-saved.
3. If the window says "Restart the service to use the new folder." (or "...to apply your changes."), press **Restart
   service**. It is refused while a recording, a processing job or a summary is active; try again when idle.

### Fields

| Field | Values | Applies |
|---|---|---|
| Run summaries | On this computer, In the cloud | at once |
| Local model (shown for "On this computer") | Picked from the models `ollama list` shows | at once |
| Provider (shown for "In the cloud") | OpenCode Go | at once |
| API key (cloud) | Set, replace or remove; never shown back | at once (stored in the keyring) |
| Cloud model (shown for "In the cloud") | Picked from the provider's list, behind the API key | at once |
| Summary language | Meeting language, or a fixed language | at once |
| Meetings folder | An absolute, writable folder | after a service restart |

"At once" means the next summary uses the new value; a summary already running finishes with the value it started
with. Changing the meetings folder never moves, copies or deletes meetings in the old folder.

Choosing **In the cloud** is the whole opt-in: there is no separate switch. The button still asks for confirmation
before every cloud request, and a transcript leaves this computer only when you confirm it.

### Where settings are stored and what wins

Settings are saved by the service in `$XDG_CONFIG_HOME/meeting-scribe/settings.json` (default
`~/.config/meeting-scribe/settings.json`, mode 0600). You are not expected to edit it, and it is read at service
start and rewritten by the next Save.

Environment variables and `.env` in the same folder remain a **developer override**. Precedence, highest first:
process environment, `.env`, `settings.json`, built-in default. A field set by the environment or `.env` is shown
read-only with the source that locks it; remove that line (and restart the service) to change it in the window.
A `SUMMARIES_PROVIDER=cloud` line without `SUMMARIES_CLOUD_ENABLED=true` is held at Local and locked; see
[`summaries.md`](summaries.md).

### API key

The key is never a setting and never goes through D-Bus or a file: the window stores it in the GNOME Keyring
(`Set API key...`, `Remove key`) and shows only "Key stored", "No key stored", "Keyring locked" or "Keyring
unavailable". The manual alternative is [`store-cloud-api-key.md`](store-cloud-api-key.md).

### Model lists

- **Local**: the installed Ollama models. A model name is never typed.
- **Cloud**: the provider's models, requested only when a key is stored ("Set the API key first." until then). Models
  this version cannot use are listed under **Not supported yet** and cannot be selected.
- A saved model that is no longer in the list is kept and flagged, never replaced silently. Use the refresh button
  to ask again, for example after `ollama pull`.

## Verify

- Reopen Preferences: the fields show what you saved and the dialog has no error line.
- Press **Generate summary** on a processed meeting: the caption shows the provider you chose.

## Rollback / troubleshooting

| Symptom | Cause and fix |
|---|---|
| "The Meeting Scribe service is not running" | `systemctl --user start meeting-scribe.service`; check `journalctl --user -u meeting-scribe.service`. |
| Preferences does not open from the gear | The review app is not installed or its launch failed; run `make install`, or start `~/.local/share/meeting-scribe/current/bin/meeting-scribe-review --preferences`. |
| Local model list empty, "Ollama is not reachable" | Ollama is stopped: `sudo systemctl start ollama` (or `ollama serve`), then press refresh. The saved model is kept. |
| "No models are installed in Ollama." | `ollama pull qwen3:4b-instruct-2507-q4_K_M`, then refresh. |
| Cloud model list disabled, "Set the API key first." | Press **Set API key...**; "Keyring locked" means unlock the login keyring first. |
| "The provider rejected the API key" | Set the key again; check the subscription. |
| A field is read-only with "developer override" | Remove `SUMMARIES_*` / `STORAGE_*` from the environment or `.env`, restart the service. |
| "Your settings were saved, but a change could not be applied" | Press **Restart service**, or `systemctl --user restart meeting-scribe.service`. |
| Restart refused ("busy") | A recording, processing job or summary is active; wait and retry. |
| Settings file damaged | The service starts with defaults and logs `SETTINGS_FILE_INVALID`; the next Save moves the bad file to `settings.json.invalid` and writes a new one. |

Codes are listed in [`../architecture/error-codes-catalog.md`](../architecture/error-codes-catalog.md).
`make uninstall` leaves `settings.json` and `.env` untouched, and the keyring entry stays (remove it in Seahorse).
