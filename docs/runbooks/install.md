---
type: runbook
status: active
last_updated: 2026-10-12
phase: F6
verifiable_by:
  - install.sh
  - uninstall.sh
  - scripts/install.sh
  - scripts/uninstall.sh
  - scripts/preflight.sh
  - tests/unit/test_install_scripts.py
  - tests/unit/test_preflight.py
  - tests/unit/test_readme_prerequisites.py
ai_instructions:
  - "Run ./install.sh --check (or --check --json) freely: it is read-only. NEVER run ./install.sh without --check, ./uninstall.sh, make install, make uninstall or sudo without the user's explicit go."
  - "Never run a fix command that starts with sudo yourself; print it for the user. Human-only steps (HUMAN) are handed back to the user, never attempted."
  - "Never read or print the Hugging Face token, .env, ~/.config/meeting-scribe/ or the meetings directory. Never press Download models: it downloads about 1.65 GiB."
---

# Install, update and troubleshoot meeting-scribe

## Purpose

Install the service, the review app and the GNOME extension from a clone of this repository, update them with
`git pull`, remove them, and fix what goes wrong. The installer first checks every prerequisite without changing
anything, never runs `sudo` and never asks a question: it prints the commands and you (or an AI agent, with your
consent) run them. Design: [`../specs/install.md`](../specs/install.md), [ADR-010](../decisions/010-install-never-uses-sudo.md),
[ADR-011](../decisions/011-hugging-face-token-in-keyring-and-models-from-preferences.md).

## Preconditions

- Fedora with GNOME on Wayland, an NVIDIA GPU with at least 8 GB of VRAM and a working driver, and a clone of this
  repository. The full list is the prerequisites table in the [README](../../README.md); the preflight tests every row.
- A terminal opened inside the GNOME session (not over SSH).

## Steps

1. Check the machine (changes nothing, calls no service):

   ```bash
   ./install.sh --check
   ```

   Every line starts with a tag and a check id. A blocker is followed by `why:` and `fix:` (a command to run) or
   `steps:` (what only you can do). Run the fix commands, then run `--check` again until the summary says
   `Summary: nothing blocks the install.`
2. Install:

   ```bash
   ./install.sh
   ```

   `make install` is an alias (`make install ARGS=--check` passes the option). The install repeats the preflight and
   stops with zero changes when something blocks. Then it refuses while a recording is in progress (stop it from the
   top-bar menu and run it again). `FORCE=1 ./install.sh` overrides only an unreadable recording state, never a recording.
3. Read the summary. `service: active`, `extension: enabled` (or "will appear after you log out and back in": on Wayland a
   new extension is discovered after one log out and log in), `review app: ready`, and the `models:` line.
4. Add the speech models (human steps, from Preferences). The line `models: PENDING` is not a failure: recording works
   at once, processing waits for the models.
   1. Open Preferences from the Meeting Scribe menu in the top bar.
   2. In **Speech models**, press **Accept the pyannote terms** and accept them in the browser, signed in to Hugging Face.
   3. Create a read token on Hugging Face (Settings, Access Tokens), paste it in **Hugging Face token** and apply it.
      It is stored in the GNOME Keyring and never leaves it except to the download process.
   4. Press **Download models** (about 1.65 GiB). The group shows step 1 of 2 and step 2 of 2 and has **Cancel**.

   If you already ran `hf auth login`, you can skip the token: with no keyring token the download uses that login.
   Details of the window: [`preferences.md`](preferences.md).

## Options and exit codes

| Option | Effect |
|---|---|
| `--check` | Run only the preflight: nothing is created, written or called for writing, and the service is never called |
| `--json` | With `--check` only: one JSON document on stdout, the human report on stderr. Alone it is a usage error (exit 2) |
| `--help` | Usage and exit codes |

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | Success: installed (models may be pending), or `--check` found nothing that blocks | Nothing |
| 1 | An install stage failed or was refused: recording in progress or its state unreadable, the `systemctl --user daemon-reload` failed, the service did not start, the extension step failed | Read the summary; see Troubleshooting |
| 2 | Usage error (unknown option, `--json` without `--check`) | Fix the command line |
| 10 | Blocked by the preflight: at least one `FIX` item | Run the printed commands, run `--check` again |
| 11 | Blocked by the preflight: only `HUMAN` items remain | Only you can resolve them; an agent hands them back |

For agents, `./install.sh --check --json` prints one document:
`{"schema_version": 1, "result": "ok" | "blocked", "exit_code": N, "os": "fedora-44", "checks": [...]}`. Each check has
`id`, `status` (`OK`, `WARN`, `FIX`, `HUMAN`, `SKIP`), `human` (true for `HUMAN`), `summary`, `why`, `fix` (commands, empty
for `HUMAN`), `steps` (manual steps, only for `HUMAN`) and `detail` (one informational line). `WARN` and `SKIP` never block.
The ids are stable; the fields only grow in a later `schema_version`.

Environment: `FORCE=1` (above), `REVIEW_APP_PYTHON` (the system interpreter the review app and the toolkit checks use,
default `/usr/bin/python3`), `MEETING_SCRIBE_NVIDIA_SMI_TIMEOUT` (default 10 s), `MEETING_SCRIBE_OLLAMA_TIMEOUT`
(default 3 s), `MEETING_SCRIBE_MODELS_CHECK_TIMEOUT` (default 30 s), `SUMMARIES_LOCAL_BASE_URL` (a non-default Ollama address) and `HF_HUB_CACHE` or `HF_HOME` (where `disk.space` looks for the model cache).

## Check ids and fixes

`FIX` means a command resolves it (printed, never run by the installer). `HUMAN` means no command does. `WARN` is optional.

| Check id | Status when it fails | Fix or steps |
|---|---|---|
| `session.wayland` | HUMAN | Log out, choose "GNOME" (not "GNOME on Xorg") on the login screen, run the installer from a terminal in that session |
| `session.gnome-shell` | HUMAN | Use a GNOME session with the Shell version listed in the extension's `metadata.json` (`shell-version`, today 50) |
| `session.bus` | HUMAN | Run the installer from a terminal inside the GNOME session (not over SSH or from a tool without the session variables) |
| `tool.uv` | FIX | `sudo dnf install uv` |
| `tool.curl` | FIX | `sudo dnf install curl` |
| `tool.ffmpeg` | FIX | `sudo dnf install ffmpeg-free` (the RPM Fusion `ffmpeg` works instead) |
| `tool.pactl` | FIX | `sudo dnf install pulseaudio-utils` |
| `tool.systemctl` | FIX | `sudo dnf install systemd` |
| `tool.gdbus` | FIX | `sudo dnf install glib2` |
| `tool.gnome-extensions` | FIX | `sudo dnf install gnome-shell` |
| `gtk.pygobject` | FIX | `sudo dnf install python3-gobject` (the three `gtk.*` items below are skipped until it passes) |
| `gtk.gtk4` | FIX | `sudo dnf install gtk4` |
| `gtk.libadwaita` | FIX | `sudo dnf install libadwaita` |
| `gtk.libsecret` | FIX | `sudo dnf install libsecret` |
| `gtk.gstreamer` | WARN | `sudo dnf install gstreamer1-plugins-good` (plays the voice samples) |
| `tool.secret-tool` | WARN | `sudo dnf install libsecret` (stores a key from a terminal) |
| `gpu.driver` | HUMAN | Install the NVIDIA driver (for example from RPM Fusion), reboot, check that `nvidia-smi` lists your GPU |
| `gpu.vram` | HUMAN | No command: transcription and diarization need at least 8000 MiB of VRAM (an "8 GB" card reports 8151 to 8192 MiB) |
| `disk.space` | FIX (WARN when the free space cannot be measured) | Free space on the named filesystem; `du -xsh ~/.cache ~/.local/share` shows what is large. Needs 8 GiB for one new service environment and 2 GiB for the models when they are not cached; directories on one filesystem are added |
| `ollama.installed` | FIX | `xdg-open https://ollama.com/download`; its install script asks for administrator rights, so read it and run it yourself |
| `ollama.reachable` | FIX | `sudo systemctl start ollama` when Ollama is a system service, else `ollama serve` in a terminal |
| `ollama.model` | FIX | `ollama pull <model>` (the default `qwen3:4b-instruct-2507-q4_K_M` is about 2.5 GB); the installer never pulls |

Human-only steps in the whole flow: the three `session.*` items, `gpu.driver`, `gpu.vram`, accepting the pyannote
terms, creating the Hugging Face token, and the one log out and log in that makes GNOME Shell discover a new extension.

## Update

```bash
git pull
./install.sh --check
./install.sh
```

The update is idempotent. It builds a new `versions/<timestamp>-<pid>/` directory, switches `current` atomically,
restarts the service when it is idle (never during a recording: the restart is skipped with a warning and the new version
applies on the next restart or login), and prunes the old version only after the new one runs. Running it again leaves
the same unit file, activation file and extension, and one version directory. It never touches your meetings,
`settings.json`, `.env`, the keyring items or the downloaded models. If the new version cannot start, the installer rolls
back to the previous one, keeps the failed directory for inspection and exits 1. After an update that adds a D-Bus
interface the Preferences window may ask you to restart the service: `systemctl --user restart meeting-scribe.service`.

## Uninstall

```bash
./uninstall.sh
```

(`make uninstall` is an alias.) It stops and disables the unit, removes the unit, the activation file, the desktop entry,
the extension and the installed versions, and prints what it keeps. It refuses while a recording runs
(`FORCE=1 ./uninstall.sh` overrides that, by intent).

Kept (not removed):

- your meetings (the meetings folder is never touched)
- settings.json and .env in `~/.config/meeting-scribe`
- the keyring items: the cloud API key and the Hugging Face token (remove them in Seahorse)
- the downloaded models in ~/.cache/huggingface, or $HF_HOME when set (shared with other tools)

## Verify

- `./install.sh --check` ends with `Summary: nothing blocks the install.` and exit 0.
- `systemctl --user is-active meeting-scribe.service` prints `active`.
- `gdbus introspect --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service` lists
  `dev.meetingscribe.Models1` (a service older than the app does not).
- In Preferences, **Speech models** says Ready once both models are cached.

## Rollback / troubleshooting

| Symptom | Cause and fix |
|---|---|
| Exit 10 | At least one `FIX` item: run the printed commands, then `./install.sh --check` |
| Exit 11 | Only `HUMAN` items remain: do the printed steps yourself, then `./install.sh --check` |
| "a recording is in progress" (exit 1) | Stop the recording from the top-bar menu, then run the installer again |
| "the recording state could not be read" (exit 1) | Stop any recording, then `systemctl --user stop meeting-scribe.service` and run again, or `FORCE=1 ./install.sh` |
| `service: NOT active` | Another instance (for example `make run`) owns the bus name: stop it with Ctrl+C, then `systemctl --user restart meeting-scribe.service`. Otherwise read `journalctl --user -u meeting-scribe.service` |
| "rolled back" in the summary | The new version failed to start; the previous one runs. The new directory is kept in `versions/` for inspection; read the journal, fix the cause, run `./install.sh` again |
| `extension: installed, will appear after you log out and back in` | Log out and back in once so GNOME Shell discovers the extension |
| `extension: FAILED (...)` | Read the cause in the summary (`gnome-extensions pack` or `install` failed), see [`gnome-extension.md`](gnome-extension.md) |
| `models: PENDING` | Expected after a first install: add the token and press Download models in Preferences. A meeting recorded meanwhile fails with `MODEL_UNAVAILABLE`; press Reprocess after the download |
| Download fails with `DIARIZATION_MODEL_ACCESS_DENIED` | The terms were not accepted with the account that owns the token, or the token is wrong or revoked: accept the terms, paste a valid token, press Download models again |
| Download fails with `MODEL_UNAVAILABLE` | No network or Hugging Face is unreachable: check the connection and press Download models (it resumes) |
| Download fails with `MODELS_TOKEN_KEYRING_LOCKED` | Unlock the login keyring (the service never prompts), then press Download models |
| Download fails with `MODELS_DOWNLOAD_TIMEOUT` | It ran past `MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS` (default 3600); press Download models again, it resumes |
| `MODELS_DOWNLOAD_ACTIVE` | A download is already running; wait for it or press Cancel |
| The Models status stays pending after the download | A partial blob (`*.incomplete`) in the Hugging Face cache blocks READY; press Download models once more, which clears the leftovers |
| An existing `hf auth login` | It is used when no keyring token exists, so Download works without storing a token |
| Preferences says the service must be restarted | The service is older than the app: `systemctl --user restart meeting-scribe.service` |
| `ollama.model` blocks after a model change | Preferences changed `summaries.local_model` or `.env` names another model: `ollama pull <model>`, or change the setting |

More: [`processing.md`](processing.md) (models and processing failures by error code), [`preferences.md`](preferences.md),
[`review-app.md`](review-app.md), [`summaries.md`](summaries.md) and [`gnome-extension.md`](gnome-extension.md).
