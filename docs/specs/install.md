---
type: spec
feature: install
status: draft  # draft | approved | in-progress | done | blocked
mvp: true
prd_ref: FR-7, NFR-1, NFR-3, NFR-6, safety requirements 1-3
depends_on:
  - decisions/001-thin-gnome-extension-dbus-service.md (thin clients over the D-Bus session bus, no HTTP)
  - decisions/005-review-ui-native-gtk-app.md (the GTK4 + libadwaita app on system Python)
  - decisions/007-gpu-worker-subprocess.md (the worker runs offline in its own process)
  - decisions/009-cloud-summaries-through-opencode-go.md (a secret lives in the GNOME Keyring and never crosses D-Bus)
  - decisions/010-install-never-uses-sudo.md (proposed with this spec)
  - decisions/011-hugging-face-token-in-keyring-and-models-from-preferences.md (proposed with this spec)
  - specs/processing.md (models, offline worker, error codes)
  - specs/preferences.md (the window that gets the "Speech models" group)
  - architecture/api-contracts.md (Models1 is added to it)
verifiable_by:
  - tests/unit/test_preflight.py
  - tests/unit/test_install_scripts.py
  - tests/unit/test_readme_prerequisites.py
  - tests/unit/test_prefetch.py
  - tests/unit/speakers/test_pyannote_backend.py
  - tests/unit/model_setup/
  - tests/e2e/model_setup/test_models_over_dbus.py
  - tests/unit/review_app/test_models_presenter.py
last_updated: 2026-10-12
ai_instructions:
  - "This spec is a contract. Do NOT implement anything that is not in it."
  - "If you need to change the spec, update this doc FIRST and ask for confirmation."
  - "Every acceptance criterion must have a test that verifies it. Test names marked (planned) do not exist yet."
  - "The installer never runs sudo and never prompts. It prints commands; a human, or an AI with the human's consent, runs them."
  - "The Hugging Face token is never an argument, never in a D-Bus reply, never logged and never a setting (BR-005 applies to it as to the cloud API key)."
  - "Tests never touch the real HOME, keyring, Hugging Face cache, GPU, Ollama, meetings folder or an audio device; they use a sandbox HOME, fake tools on PATH, a fake key store and a private bus."
  - "Never run the real install, uninstall or model download from an agent without the user's explicit go."
---

# Spec: Robust install from the repo

## Goal (one sentence)

A person, or an AI agent working for them, clones the repo, runs `./install.sh --check`, fixes what it reports,
runs `./install.sh`, and ends with a working system or a precise list of what is missing, without the installer ever
changing the machine half-way, running `sudo` or prompting.

---

## Users / Personas

| Persona | How they use the feature |
|---|---|
| The user (single user, Fedora, GNOME on Wayland) | Clones the repo, runs the preflight, installs what it names, installs, adds the Hugging Face token in Preferences and presses Download models |
| An AI agent acting for the user | Runs `./install.sh --check --json`, reads the items, runs the `fix` commands only with the user's consent, and hands the human-only steps back to the user |
| The developer | Keeps `make setup`, `make run` and the environment overrides; `make install` stays an alias of `./install.sh` |

---

## User stories

- **US-1**: As the user, I want one command that tells me everything that is missing before it changes anything, so that I never end up with a half-installed system.
- **US-2**: As the user, I want each missing item to say what is missing, why it is needed and the exact command to fix it, so that I do not search the web.
- **US-3**: As an AI agent, I want stable exit codes and a JSON report, so that I can decide the next step without parsing prose.
- **US-4**: As the user, I want the steps only I can do (NVIDIA driver and reboot, the Hugging Face terms and token) labelled apart from the ones a command fixes, so that an agent never pretends to do them.
- **US-5**: As the user, I want the install never to ask me anything mid-way and never to run `sudo`, so that I can read every privileged command before I run it.
- **US-6**: As the user, I want to add my Hugging Face token in Preferences and press Download models, so that I never touch `hf auth login` or a console for it.
- **US-7**: As the user, I want recording to work right after the install, even while the models are still missing.
- **US-8**: As the user, I want to update with `git pull` and `./install.sh`, and keep my meetings, settings, keyring entries and models.
- **US-9**: As the user, I want uninstall to say what it keeps.

---

## Acceptance criteria (Gherkin to tests)

Every criterion runs against a sandbox HOME with fake tools on PATH (the pattern of `tests/unit/test_install_scripts.py`),
a fake key store and a private bus. No test uses the real machine state, GPU, Ollama, keyring, network or audio device.

### AC-1: `--check` changes nothing

```gherkin
GIVEN a sandbox HOME and fake tools that record every call
WHEN I run ./install.sh --check, with every prerequisite present and with some missing
THEN no file or directory is created under HOME, no systemctl enable/start/restart, uv tool install, gnome-extensions install or enable call is made, and no recording-state call changes anything
AND the exit code is 0 when nothing blocks and 10 or 11 otherwise
```

**Test**: `tests/unit/test_preflight.py::test_check_changes_nothing` (planned)

### AC-2: A missing prerequisite stops the install with zero changes

```gherkin
GIVEN one or more prerequisites are missing
WHEN I run ./install.sh
THEN the preflight report is printed, the exit code is 10 or 11, and the sandbox HOME is byte-for-byte unchanged
AND uv, systemctl and gnome-extensions were never called for a write
```

**Test**: `tests/unit/test_install_scripts.py::test_install_stops_with_zero_changes_when_a_prerequisite_is_missing` (planned)

### AC-3: Every item says what, why and how to fix it

```gherkin
GIVEN a blocking item
THEN the report gives its stable id, what is missing, why meeting-scribe needs it and at least one exact fix command
AND a human-only item gives the manual steps instead and is labelled HUMAN, not FIX
```

**Test**: `tests/unit/test_preflight.py::test_every_blocking_item_has_what_why_and_fix` (planned)

### AC-4: The installer never runs sudo and never prompts

```gherkin
GIVEN a fake sudo on PATH that records its calls, and standard input closed
WHEN I run ./install.sh and ./install.sh --check, with and without missing prerequisites
THEN sudo is never called, no read from the terminal happens, and the run ends without waiting for input
AND "sudo" appears in the output only inside printed fix commands
```

**Test**: `tests/unit/test_preflight.py::test_sudo_is_never_called_and_nothing_prompts` (planned)

### AC-5: Exit codes are stable

```gherkin
GIVEN the documented exit codes 0, 1, 2, 10 and 11
THEN 0 is success (install done, or --check with nothing blocking), 1 is an install-stage failure or a refusal (recording in progress, service or extension step failed), 2 is a usage error, 10 is "blocked: at least one FIX item" and 11 is "blocked: only HUMAN items remain"
AND the same input always yields the same code
```

**Test**: `tests/unit/test_preflight.py::test_exit_codes_match_the_contract` (planned)

### AC-6: The JSON report is stable and alone on stdout

```gherkin
GIVEN --json
WHEN the preflight runs
THEN stdout holds exactly one JSON document with schema_version 1, result, exit_code and a checks array whose items have id, status, summary, why, fix, human
AND nothing else is written to stdout, and the document parses with a strict JSON parser even when a value holds a quote or a backslash
AND every id in the document is one of the documented check ids
```

**Test**: `tests/unit/test_preflight.py::test_json_report_is_one_valid_document` (planned)

### AC-7: Human-only steps are labelled apart

```gherkin
GIVEN no NVIDIA driver, a GPU with less than 8000 MiB, or a session that is not GNOME on Wayland
THEN those items have status HUMAN, the report says no command fixes them or lists the manual steps, and the exit code is 11 when they are the only blockers
GIVEN a missing package (for example libadwaita)
THEN its status is FIX and the exit code is 10
```

**Test**: `tests/unit/test_preflight.py::test_human_only_items_are_labelled_and_exit_11` (planned)

### AC-8: The GPU checks read nvidia-smi only

```gherkin
GIVEN a fake nvidia-smi that prints "name, memory.total [MiB], driver_version" rows
THEN gpu.driver is OK when a GPU is listed and HUMAN when the tool is missing or fails
AND gpu.vram is OK at 8000 MiB or more (the measured RTX 5060 Laptop reports 8151 MiB) and HUMAN below it
AND the detail line names the GPU, the memory and the driver version
```

**Test**: `tests/unit/test_preflight.py::test_gpu_and_vram_checks` (planned)

### AC-9: The disk check is measured per filesystem

```gherkin
GIVEN a fake df that reports the free bytes of the data directory and of the Hugging Face cache directory
THEN the required space is 8 GiB for the service on a first install, 16 GiB when a version already exists (two environments coexist during an upgrade), and 2 GiB for the models when they are not cached
AND the requirements of directories on the same filesystem are added, the others are checked apart
AND a shortage is a FIX item that names the filesystem, the free and the required space and a command to see what is large
```

**Test**: `tests/unit/test_preflight.py::test_disk_requirement_is_per_filesystem` (planned)

### AC-10: GNOME on Wayland, and the shell version the extension declares

```gherkin
GIVEN XDG_SESSION_TYPE is not wayland, or gnome-shell is missing, or its major version is not in the "shell-version" list of the extension metadata (today ["50"])
THEN session.wayland, session.gnome-shell are HUMAN items that state what was found and what is required
AND the required versions are read from metadata.json, not repeated in the script
```

**Test**: `tests/unit/test_preflight.py::test_session_checks_read_the_extension_metadata` (planned)

### AC-11: The review app toolkit blocks the install

```gherkin
GIVEN the system Python cannot import gi, or cannot load Gtk 4.0, Adw 1 or Secret 1
THEN gtk.pygobject, gtk.gtk4, gtk.libadwaita or gtk.libsecret is a FIX item with the dnf package name
AND the check runs only the system interpreter (REVIEW_APP_PYTHON), never the uv environment
```

**Test**: `tests/unit/test_preflight.py::test_gtk_stack_items` (planned)

### AC-12: Ollama missing, unreachable or without the default model stops the install

```gherkin
GIVEN ollama is not installed
THEN ollama.installed is FIX with the download page, and ollama.reachable and ollama.model are SKIP
GIVEN it is installed but GET http://127.0.0.1:11434/api/tags fails or times out
THEN ollama.reachable is FIX with "systemctl start ollama" (or "ollama serve")
GIVEN it answers but the configured local model is not in the list
THEN ollama.model is FIX with "ollama pull <model>", the model being the one the service would use (environment, then .env, then settings.json, then the default)
AND in every case the install stops with zero changes and the installer itself never pulls or starts anything
```

**Test**: `tests/unit/test_preflight.py::test_ollama_items` (planned)

### AC-13: One entry point

```gherkin
GIVEN the repository root
THEN ./install.sh and ./uninstall.sh exist, are executable and are thin wrappers over scripts/install.sh and scripts/uninstall.sh
AND "make install" and "make uninstall" run them and pass ARGS through (for example make install ARGS=--check)
AND every doc and message that names the command names ./install.sh (or make install as the alias)
```

**Test**: `tests/unit/test_install_scripts.py::test_root_wrappers_and_make_aliases` (planned)

### AC-14: Prerequisites are listed in the README and checked by the preflight

```gherkin
GIVEN the preflight check ids
THEN the README prerequisites table mentions every id that can block, and every row of the table maps to a check id
AND a new check id without a README row (or the reverse) fails the test
```

**Test**: `tests/unit/test_readme_prerequisites.py::test_readme_and_preflight_list_the_same_prerequisites` (planned)

### AC-15: Processing loads the cached models with no token

```gherkin
GIVEN the models are in the cache, HF_HUB_OFFLINE=1, no Hugging Face login and no token in the environment
WHEN the diarize worker loads the pipeline
THEN the loader passes no token (token=None) and the load succeeds from the cache
AND the same holds when a login exists (the loader never reads it)
```

**Test**: `tests/unit/speakers/test_pyannote_backend.py::test_load_pipeline_passes_no_token` (planned); manual check in the runbook (section 6)

### AC-16: The token reaches only the download process, and only on stdin

```gherkin
GIVEN a token stored in a fake key store
WHEN Models1.Download starts the download process
THEN the token is written to the child's standard input, one line
AND it is absent from the child's argv, environment, from every log line, every D-Bus reply and signal, and every error message
AND anything shaped like a token (hf_...) in the child's output is scrubbed before it is logged
```

**Test**: `tests/unit/model_setup/test_download_runner.py::test_token_goes_only_to_stdin` (planned), `tests/e2e/model_setup/test_models_over_dbus.py::test_token_never_crosses_dbus` (planned)

### AC-17: The keyring token wins over the ambient login, and the login still works

```gherkin
GIVEN a token in the keyring
THEN Download uses it explicitly and does not read the ambient login
GIVEN no token in the keyring (missing, or no Secret Service)
THEN Download runs the fetch with the ambient login (an existing "hf auth login" keeps working)
GIVEN the keyring is locked
THEN Download fails with MODELS_TOKEN_KEYRING_LOCKED and never prompts
```

**Test**: `tests/unit/model_setup/test_download_models.py` (planned)

### AC-18: Download models reports status, progress and the end

```gherkin
GIVEN models that are not cached
WHEN a client calls Models1.Download
THEN the reply is DOWNLOADING at once, ModelsProgress(model, step, total_steps) is emitted per step, and ModelsFinished(state, error_code) ends it with READY, FAILED or CANCELLED
AND GetStatus reports PENDING, DOWNLOADING, READY or FAILED with whisper_cached, pipeline_cached and token_stored
AND a second Download while one runs fails with MODELS_DOWNLOAD_ACTIVE, and CancelDownload stops the child
```

**Test**: `tests/unit/model_setup/test_download_models.py`, `tests/e2e/model_setup/test_models_over_dbus.py` (planned)

### AC-19: Preferences stores the token, links the terms and downloads

```gherkin
GIVEN the Preferences window
THEN a "Speech models" group shows the models state, a write-only "Hugging Face token" field (stored indicator, never the value), a link to the pyannote terms page and a "Download models" button with progress and a cancel action
WHEN the user pastes a token and saves
THEN it is stored in the GNOME Keyring through libsecret (application meeting-scribe, key huggingface-token), never sent to the service
AND pressing Download models calls Models1.Download and follows the signals
```

**Test**: `tests/unit/review_app/test_models_presenter.py`, `tests/unit/review_app/test_hf_token_store.py` (planned)

### AC-20: The install reports "models pending" and does not fail

```gherkin
GIVEN every prerequisite is present and the models are not cached
WHEN I run ./install.sh
THEN the service, unit, activation file, review app and extension are installed, the exit code is 0
AND the summary says "models: PENDING: add your Hugging Face token in Preferences and press Download models" and recording works
GIVEN the models are cached
THEN the summary says "models: ready (cached)"
```

**Test**: `tests/unit/test_install_scripts.py::test_install_reports_models_pending_and_still_succeeds` (planned)

### AC-21: Update is idempotent and keeps user data

```gherkin
GIVEN an installed system with meetings, settings.json, a keyring entry and cached models
WHEN I run ./install.sh again (after git pull)
THEN a new version directory becomes current, the service restarts when idle, and the meetings, settings.json, keyring entries and model cache are untouched
AND running it a third time changes nothing that matters (same units, same files, old versions pruned)
```

**Test**: `tests/unit/test_install_scripts.py::test_update_keeps_user_data_and_is_idempotent` (planned)

### AC-22: Uninstall mirrors the entry point and says what it keeps

```gherkin
WHEN I run ./uninstall.sh
THEN it removes what install created, keeps meetings, settings.json, .env, the keyring entries (cloud key and Hugging Face token) and the downloaded models, and prints that list
AND the keep-list is the same text in the runbook
```

**Test**: `tests/unit/test_install_scripts.py::test_uninstall_lists_what_it_keeps` (planned)

---

## What this is NOT (out of scope for this spec)

- Not distribution through extensions.gnome.org (see [Future note](#future-note-extensions-gnome-org)), an RPM or a COPR package.
- Not a cloud-only install mode: transcription and diarization need the local NVIDIA GPU, so the cloud only replaces the summary step (owner decision).
- Not an installer that runs `sudo`, installs system packages, installs the NVIDIA driver, installs Ollama, pulls an Ollama model or starts Ollama.
- Not a prompt of any kind during the install, including for the Hugging Face token.
- Not a new setting for the token: it is a secret, not a setting.
- Not the F5 leftovers (summary quality, Preferences polish): a separate phase after F6.
- Not support for distributions other than Fedora with `dnf`: the report prints Fedora package names and `dnf` commands; other distributions can read the package names.
- Not an automatic reprocess of meetings that failed with `MODEL_UNAVAILABLE` while the models were pending: the user presses Reprocess (the menu already offers it).

---

## Edge cases

| Case | Expected behavior |
|---|---|
| The installer is run over SSH, from a TTY or from a tool without the session variables | `session.wayland` and `session.bus` are HUMAN items that say to run it from a terminal inside the GNOME session |
| `nvidia-smi` hangs | Bounded by a timeout (10 s); the item is HUMAN with "nvidia-smi did not answer" |
| Several GPUs | The first row decides; the detail line lists the others; VRAM is the largest total |
| Free VRAM is lower than total (a game or browser holds some) | Not checked: the total is the requirement; the report prints the free amount as information |
| `df` cannot read a directory that does not exist yet (`~/.local/share/meeting-scribe` on a first install) | The nearest existing parent is measured |
| Ollama answers on a non-default address | `SUMMARIES_LOCAL_BASE_URL` from the environment is honoured (the `/v1` suffix is stripped); the Preferences file has no such field |
| Ollama's model store is on another filesystem or owned by a system service | Not measured (it cannot be found without privileges); `ollama pull` fails with its own clear message when the disk is full |
| The summary provider is `cloud` | Local Ollama is still required (owner decision: the install stops if it is missing) |
| A recording is in progress, or its state is unreadable | Unchanged from today: refusal with exit 1 after a clean preflight; `FORCE=1` keeps its current meaning. `--check` never calls the service |
| `--check` and `--json` together | A single JSON document, same exit codes |
| Unknown option | Usage on stderr, exit 2, nothing changed |
| Models half downloaded (interrupted) | `GetStatus` is PENDING; Download resumes through the Hugging Face cache; a partial blob is never counted as cached |
| Token invalid or revoked | The fetch fails with `DIARIZATION_MODEL_ACCESS_DENIED`; the window says to replace the token or accept the terms; no silent fall back to the ambient login |
| Keyring unavailable (no Secret Service) while a login exists | Download uses the login; the status says the keyring is not available |
| Terms not accepted | Same `DIARIZATION_MODEL_ACCESS_DENIED`; the window links the terms page |
| Offline during Download | `MODEL_UNAVAILABLE` with the network hint; nothing partial is reported as ready |

---

## Open questions

Only what could not be decided from the code and the owner decisions of 2026-10-12.

- **OQ-1: Progress granularity.** The Download models action shows step-level progress: the pyannote pipeline, then Whisper (2 steps, an indeterminate bar within a step), about 1.65 GiB in total. Byte-level progress needs a `tqdm` hook into `huggingface_hub` and adds code and a signal rate to throttle. Is step-level enough? Recommendation: yes.
- **OQ-2: Existing `hf auth login` users.** Today `make install` downloads the models with the ambient login. Under this spec the installer never downloads: the models arrive from Preferences (Download models), which still uses the ambient login when no keyring token exists. A user who already has a login and no cached models therefore presses one button after installing. Should `./install.sh` instead keep trying the ambient login download as a visible, bounded step? Recommendation: no, one rule for everyone and no hidden network step.
- **OQ-3: Freeze the D-Bus names at the end of F6?** `api-contracts.md` and the error catalog say the bus name, the object path and the `dev.meetingscribe.Error.` prefix are "proposed and renamable until F6" (CR-011). Do we freeze them when F6 closes (change those sentences to "stable", and any later rename needs an ADR)? Recommendation: yes.

---

## Technical design

### 1. What exists today (baseline, read from the code on 2026-10-12)

| Piece | Today |
|---|---|
| `scripts/install.sh` | The whole install, no preflight. Refuses on a recording or an unreadable state (`refuse_unless_safe`). Installs the service as a versioned `uv tool` (Python 3.12, `meeting-scribe[ml] @ <repo>`) in `$XDG_DATA_HOME/meeting-scribe/versions/<id>/`, the review app copy and launcher, the unit, the activation file, switches `current` atomically, starts or restarts the service, rolls back when it cannot start, prunes old versions, installs the desktop entry, then downloads the models (`prefetch-models` bounded by `MEETING_SCRIBE_PREFETCH_TIMEOUT`, default 3600 s), packs and enables the extension. It then probes the review app toolkit and Ollama read-only and prints a summary. Every prerequisite gap only warns (toolkit, Ollama, models); only the service and extension steps set a non-zero exit |
| `scripts/_common.sh` | Paths (`INSTALL_ROOT`, `CURRENT_LINK`, unit and activation paths), `REVIEW_APP_PYTHON` (default `/usr/bin/python3`), `service_state` over `gdbus`, `refuse_unless_safe` (`FORCE=1` semantics), `desktop_exec_argument` |
| `scripts/uninstall.sh` | Stops and disables the unit, removes the unit, activation file, extension, desktop entry and the versions tree, runs `uv tool uninstall meeting-scribe` (legacy). Keeps meetings, `.env`, `settings.json`; says the cloud key stays in the keyring |
| `Makefile` | `install` runs `scripts/install.sh`, `uninstall` runs `scripts/uninstall.sh`; no argument passthrough |
| `prefetch.py` and `cli.py` | `meeting-scribe prefetch-models` fetches the pipeline first, then Whisper, always attempts both, scrubs `hf_...`; the fetchers are injected. It never reads the token |
| `pyannote_backend.py` | `load_pipeline` and `fetch_pipeline` both call `Pipeline.from_pretrained(pipeline, token=True)` ("the ambient login") |
| `faster_whisper_backend.py` | `download_model` (`faster_whisper.utils.download_model`, public repo, no token); `load_model` builds `WhisperModel(model, ...)` |
| `subprocess_worker_runner.py` | Every worker child gets `HF_HUB_OFFLINE=1`, telemetry off |
| Errors | `DiarizationModelAccessError` says "run `hf auth login`"; `ModelUnavailableError` says "run `make install` again" |
| Preferences and Settings1 | `Settings1` (service) owns the settings file; the window (`review-app/.../preferences_dialog.py`) stores the cloud API key itself through libsecret (`ui/key_store.py`, attributes `application=meeting-scribe`, `key=opencode-api-key`); the service reads that key with `dbus-fast` (`secret_service_store.py`). The key is not a setting and never crosses D-Bus (BR-005, ADR-009) |
| `tests/unit/test_install_scripts.py` | 104 tests against a sandbox HOME and a PATH of fake `uv`, `systemctl`, `gnome-extensions`, `gdbus`, `python3`, `ollama` and a whitelist of coreutils |
| Extension metadata | `"shell-version": ["50"]` (only GNOME Shell 50) |

Two facts verified in the installed libraries (read from source, not run):

- `huggingface_hub` 2.2.0, `utils/_headers.py` lines 136-139: with `token=True` and no stored login it raises `LocalTokenNotFoundError`; `file_download.py` builds the headers (lines 1017 and 1659) before it looks at the cache. So today a cached model still fails to load offline when there is no login. That is the defect decision 7 asks to remove.
- `pyannote.audio` 4.0.7 `Pipeline.from_pretrained(..., token=...)` accepts a string, `True` or `None`.

### 2. Measurements (2026-10-12, no files in the Hugging Face cache were opened, sizes only)

Machine: Fedora Linux 44, kernel 7.1.13-200.fc44, GNOME Shell 50.4 on Wayland, `uv` 0.10.9, system Python 3.14.7, Ollama 0.18.1 (answers on `http://127.0.0.1:11434`), NVIDIA GeForce RTX 5060 Laptop GPU, driver 610.57.04.

| What | Command | Result |
|---|---|---|
| GPU, memory, driver | `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv` | `NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB, 610.57.04` |
| Service environment (one version) | `du -sh ~/.local/share/meeting-scribe` | 7.1 GiB (of which `tools/` 7.1 GiB, `app/` 308 KiB): torch, pyannote and the NVIDIA CUDA wheels dominate |
| Hugging Face cache, whole | `du -sh ~/.cache/huggingface` | 1.7 GiB (all in `hub/blobs`) |
| Whisper `large-v3-turbo` | `du -shL ~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo` | 1.6 GiB |
| pyannote `speaker-diarization-community-1` | `du -shL` on its folder | 32 MiB |
| Whisper `tiny` | `du -shL` on its folder | 75 MiB (a leftover from tests; the install does not fetch it) |
| Local summary model | `ollama list` | `qwen3:4b-instruct-2507-q4_K_M` 2.5 GB |
| Free disk | `df -h --output=avail ~` | 101 GiB on `/home` |
| VRAM, summary model | measured earlier the same day (owner evidence) | peak about 5 GB at a 16k context, 100% on the GPU; Whisper and pyannote never share the GPU |

Gotchas found while measuring (to carry into the runbook):

- Plain `du -sh` on a `models--*` folder shows only the snapshot symlinks (3.7 MiB for the 1.6 GiB model); the data lives in `hub/blobs`. Use `du -shL` per model, or `du -sh` on `hub`.
- `~/.ollama` is 16 KiB here: this Ollama stores models elsewhere (it runs as a system service), so the preflight cannot measure the model store.
- VRAM was measured only for the summary model in this task; Whisper and pyannote VRAM numbers come from the F3 spec, not from a new run.

Derived requirements (single constants, in one place in the preflight, covered by a test): service 8 GiB (7.1 measured plus margin), 16 GiB during an upgrade (the previous environment stays until the new one runs), models 2 GiB (1.65 measured plus margin), minimum VRAM 8000 MiB (an "8 GB" card reports 8151 to 8192 MiB; the owner's card reports 8151).

### 3. One entry point

Decision: a root `install.sh` that is a **thin wrapper** (`exec scripts/install.sh "$@"` after resolving its own directory), and a root `uninstall.sh` the same way. The implementation stays in `scripts/`.

Why not move the files: `scripts/install.sh` and `uninstall.sh` source `scripts/_common.sh` through a `REPO_ROOT` computed from their own location, 104 tests address them by path, `make test-packaging`, the runbooks and the READMEs cite them, and `git blame` follows the file. Moving buys nothing the wrapper does not give: the user types `./install.sh` and there is one entry point. The wrapper is five lines, has no logic, and the tests assert it only forwards arguments and the exit code.

`make install` becomes `./install.sh $(ARGS)` and `make uninstall` `./uninstall.sh $(ARGS)`, so `make install ARGS=--check` works.

The preflight lives in a new `scripts/preflight.sh`, sourced by `scripts/install.sh` (it is not a separate entry point; `--check` is an option of `install.sh`).

### 4. The preflight

**Order.** `install.sh` parses options, runs the preflight (read-only), then (unless `--check`) continues with today's recording-state check and install steps. Nothing is created, written or called for writing before the preflight ends with no blocker.

**Statuses.**

| Status | Meaning | Blocks the install |
|---|---|---|
| `OK` | The requirement is met | no |
| `WARN` | Optional or advisory (sample playback plugin, `secret-tool`) | no |
| `FIX` | Missing, and commands fix it; the report prints them | yes |
| `HUMAN` | Only the user can resolve it (hardware, driver and reboot, session type, shell version, accepting terms) | yes |
| `SKIP` | Depends on a failed check (for example `ollama.reachable` when Ollama is not installed) | no |

**Checks** (stable ids; the README table has one row per id that can block):

| Id | Check (non-sudo) | Status when it fails | Why it is needed | Fix (printed, never run) |
|---|---|---|---|---|
| `session.wayland` | `$XDG_SESSION_TYPE` is `wayland` | HUMAN | The extension and the review app target GNOME on Wayland | Log out, choose "GNOME" (not "GNOME on Xorg"), run the installer from a terminal in that session |
| `session.gnome-shell` | `gnome-shell --version` major is in the `shell-version` list of `gnome-extension/.../metadata.json` (today 50) | HUMAN | GNOME Shell refuses to load an extension for another version | Use GNOME Shell 50 (Fedora 44) |
| `session.bus` | `$DBUS_SESSION_BUS_ADDRESS` set or `$XDG_RUNTIME_DIR/bus` exists | HUMAN | The service, the extension and the apps talk over the session bus; `systemctl --user` needs it | Run from a terminal inside the GNOME session |
| `tool.uv` | `command -v uv` | FIX | Installs the service and its Python 3.12 environment | `sudo dnf install uv` |
| `tool.curl` | `command -v curl` | FIX | Local Ollama probe | `sudo dnf install curl` |
| `tool.ffmpeg` | `command -v ffmpeg` | FIX | Captures the audio | `sudo dnf install ffmpeg-free` (or the RPM Fusion `ffmpeg`) |
| `tool.pactl` | `command -v pactl` | FIX | Finds the microphone and the system-audio monitor | `sudo dnf install pulseaudio-utils` |
| `tool.systemctl` | `command -v systemctl` | FIX | Runs the service as a `systemd --user` unit | `sudo dnf install systemd` |
| `tool.gdbus` | `command -v gdbus` | FIX | Reads the recording state before an update | `sudo dnf install glib2` |
| `tool.gnome-extensions` | `command -v gnome-extensions` | FIX | Packs, installs and enables the extension | `sudo dnf install gnome-shell` |
| `gtk.pygobject` | `$REVIEW_APP_PYTHON -c 'import gi'` | FIX | The review app runs on the system Python | `sudo dnf install python3-gobject` |
| `gtk.gtk4` | `gi.require_version("Gtk", "4.0")` | FIX | Review app UI | `sudo dnf install gtk4` |
| `gtk.libadwaita` | `gi.require_version("Adw", "1")` | FIX | Review app UI | `sudo dnf install libadwaita` |
| `gtk.libsecret` | `gi.require_version("Secret", "1")` | FIX | Stores the cloud key and the Hugging Face token in the GNOME Keyring | `sudo dnf install libsecret` |
| `gtk.gstreamer` | the `gstreamer1-plugins-good` package is present (`rpm -q`) or a `GStreamer` plugin probe | WARN | Voice sample playback | `sudo dnf install gstreamer1-plugins-good` |
| `tool.secret-tool` | `command -v secret-tool` | WARN | Optional: store a key from a terminal (runbook) | `sudo dnf install libsecret` |
| `gpu.driver` | `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader` lists a GPU (10 s timeout) | HUMAN | Transcription and diarization run on the local NVIDIA GPU; there is no CPU or cloud mode | Install the NVIDIA driver (for example from RPM Fusion), reboot, run `nvidia-smi` |
| `gpu.vram` | the largest `memory.total` is at least 8000 MiB | HUMAN | The models and the summary model have to fit in 8 GB (the summary model peaks near 5 GB; Whisper and pyannote never share the GPU) | None: this GPU cannot run the local models |
| `disk.space` | `df` on the data directory and the Hugging Face cache directory against section 2 | FIX | The environment is 7.1 GiB, the models 1.65 GiB | Free space on the named filesystem; to see what is large: `du -xsh ~/.cache ~/.local/share` |
| `ollama.installed` | `command -v ollama` | FIX | Local summaries (and the install stops without it, owner decision) | Install from <https://ollama.com/download> (its own script asks for `sudo`: read it first, run it yourself) |
| `ollama.reachable` | `curl -s --max-time 3 http://127.0.0.1:11434/api/tags` succeeds | FIX | Summaries talk to the local Ollama API | `systemctl start ollama` (or `ollama serve` in a terminal) |
| `ollama.model` | the configured local model is in `/api/tags` (precedence: environment, `.env`, `settings.json`, default `qwen3:4b-instruct-2507-q4_K_M`, the logic `install.sh` has today) | FIX | The summary needs it; the install never pulls | `ollama pull <model>` (about 2.5 GB) |

The check for models in the cache is not a blocker: it belongs to the install summary (section 5).

**Output for humans.** One line per item, then the explanation under each blocker:

```
[ OK  ] gpu.driver          NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB, driver 610.57.04
[ FIX ] gtk.libadwaita      libadwaita (Gtk/Adw typelib) is missing for /usr/bin/python3
         why: the review app is built on libadwaita
         fix: sudo dnf install libadwaita
[HUMAN] gpu.driver          nvidia-smi is not installed
         why: transcription and diarization run on the local NVIDIA GPU
         steps: install the NVIDIA driver, reboot, check that nvidia-smi lists your GPU (nobody can do this for you)
Summary: 1 fixable, 1 human-only. Nothing was changed. Run the fix commands, then run ./install.sh --check again.
```

The status tags and ids are stable and one item starts at column 0 with a `[` tag, so a line-oriented parser works; the indented lines (`why:`, `fix:`, `steps:`) belong to the item above.

**Output for agents: `--json`.** Recommendation (decided here): one format for machines, `--json`, instead of a second line format. Human text goes to stdout in the default mode; with `--json`, stdout carries exactly one document and all prose goes to stderr:

```json
{
  "schema_version": 1,
  "result": "blocked",
  "exit_code": 10,
  "os": "fedora-44",
  "checks": [
    {"id": "gtk.libadwaita", "status": "FIX", "human": false,
     "summary": "libadwaita (Gtk/Adw typelib) is missing for /usr/bin/python3",
     "why": "the review app is built on libadwaita",
     "fix": ["sudo dnf install libadwaita"], "detail": ""}
  ]
}
```

`human` is true for `HUMAN` items, whose `fix` is empty and whose `steps` array holds the manual steps. The script writes the JSON with a small escape function (backslash, quote, control characters), because `jq` is not a prerequisite; a test parses the output with a strict parser. New fields may be added in a later `schema_version` only.

**Exit codes** (documented in the runbook and `--help`):

| Code | Meaning |
|---|---|
| 0 | Success: installed (models may be pending), or `--check` found nothing that blocks |
| 1 | The install ran or was refused at a later stage: a recording is in progress or its state is unreadable, the service did not start, the extension step failed (unchanged from today) |
| 2 | Usage error (unknown option) |
| 10 | Blocked by the preflight: at least one `FIX` item (an agent can act on it); `HUMAN` items may also be present |
| 11 | Blocked by the preflight: only `HUMAN` items remain (hand it back to the user) |

### 5. Install flow after the change

1. Parse options (`--check`, `--json`, `--help`).
2. Preflight. A blocker ends the run here: report, exit 10 or 11, zero changes. `--check` ends here with 0 when nothing blocks.
3. `refuse_unless_safe` (today's recording guard).
4. The unchanged install steps: versioned `uv tool install`, review app, unit, activation file, atomic switch, start or restart with rollback, desktop entry, pruning, extension.
5. The model download step is **removed** from the installer (and with it `MEETING_SCRIBE_PREFETCH_TIMEOUT`, which moves to the service as the download timeout, section 7). In its place the installer runs `current/bin/meeting-scribe prefetch-models --check` (offline, bounded to 30 s) and prints `models: ready (cached)` or `models: PENDING: add your Hugging Face token in Preferences and press Download models; recording works meanwhile`. Neither is a failure.
6. The summary keeps today's lines. The Ollama and toolkit lines now report what the preflight already established (they can no longer be "missing").

The install keeps its existing guarantees (versioned directories, atomic `current`, rollback, no restart during a recording).

### 6. Hugging Face token and the models

**Where the token lives.** In the GNOME Keyring, like the cloud API key (ADR-009): Secret Service item with attributes `application=meeting-scribe`, `key=huggingface-token`, label "Meeting Scribe Hugging Face token". The review app writes it through libsecret (a second `SecretKeyStore` instance with those attributes; the existing class is parametrised, not copied). It is not a setting (BR-005 extended): it is not in `settings.json`, not in `Settings1`, not in `.env`.

**Who downloads.** The service, not the app. A new `model_setup` domain runs the download as a child process (`sys.executable -m meeting_scribe.cli prefetch-models --token-stdin --progress-json`), reads the token from the keyring itself (the same on-demand `dbus-fast` read the cloud key uses, moved into the shared kernel so the two domains do not cross), writes it to the child's standard input (one line) and never anywhere else. The child passes it explicitly to `Pipeline.from_pretrained(pipeline, token=<str>)` for the gated pipeline; the Whisper model is public and needs none. The app only displays status and sends `Download`, so both clients stay thin D-Bus clients (ADR-001), and the secret crosses no D-Bus message: the app stores it in the keyring, the service reads it from the keyring.

Why stdin and not an environment variable: an environment variable is readable by the same user through `/proc/<pid>/environ` and is inherited by grandchildren; stdin is consumed and closed. Never argv (visible in `ps`).

**Which token wins.** The keyring token wins over the ambient login, when one is stored. Reasons: it is explicit and visible (the window shows "stored"), it is what the user last chose, and falling silently back to a different identity when it is invalid would hide the real problem (a revoked or wrong token). When no keyring token exists, or no Secret Service exists, the fetch uses the ambient login (`token=None`, the library's default lookup), so an existing `hf auth login` keeps working. A locked keyring is an error (`MODELS_TOKEN_KEYRING_LOCKED`); the service never prompts.

**Processing needs no token.** `RealPyannoteBackend.load_pipeline` passes `token=None`; with `HF_HUB_OFFLINE=1` a cached model loads without any network or login. `fetch_pipeline` takes the token as a parameter. The worker environment stays offline and token-free.

**Verifying "no token, offline" without the network.** Planned manual check, in the runbook, never touching the real HOME state: set `HF_HOME` to a temporary directory that links to the real `hub` folder (so there is no `token` file), unset `HF_TOKEN`, set `HF_HUB_OFFLINE=1`, and run the diarize stage on a synthetic WAV fixture (never a recording). Unit proof: a fake backend asserts `load_pipeline` is called with `token=None`. This was not run in this task: it needs the real models and the GPU.

**`prefetch-models` after the change** (the same console command, developer-usable):

- `--check`: offline; prints one JSON line `{"whisper": true, "pipeline": false}`; exit 0 when both are cached, 1 otherwise. The cache test uses `huggingface_hub` with `local_files_only`; a partial blob never counts.
- `--token-stdin`: reads one line from standard input as the token for the pipeline.
- `--progress-json`: one JSON object per line on stdout (`step`, `finished`, `error`), never containing the token; the rest of the output is scrubbed of `hf_...`.
- Without options it behaves as today (pipeline first, Whisper second, ambient login) for developers.
- Error texts change: `DiarizationModelAccessError` now says to accept the terms and add the token in Preferences, then press Download models (it keeps naming the terms page); `ModelUnavailableError` says to open Preferences and press Download models, replacing "run `make install` again".

**Preferences window ("Speech models" group, always visible).** A status row (Ready, Pending, Downloading step N of 2, Failed with the code's text), a write-only password entry "Hugging Face token" (a "stored" indicator, never the value, same pattern as the API key dialog), a link row "Accept the pyannote terms" (opens `https://hf.co/pyannote/speaker-diarization-community-1` with the system handler; if `SPEAKERS_PIPELINE` is overridden the developer knows the page), and a "Download models" button with a progress bar and a cancel action. Presenter logic stays free of `gi` and is unit-tested.

### 7. D-Bus change: new interface `dev.meetingscribe.Models1`

Same bus name and object path as the others (ADR-001: still the session bus, no HTTP, thin clients). Cross-checked against `api-contracts.md`: nothing in `Recording1`, `Meetings1`, `Summaries1` or `Settings1` changes; the token appears in no signature.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `GetStatus` | method | `() -> a{sv}` | `state` (s: `READY`, `PENDING`, `DOWNLOADING`, `FAILED`), `whisper_cached` (b), `pipeline_cached` (b), `token_stored` (b, the keyring item exists; the secret is not read), `step` (i), `total_steps` (i), `failure_code` (s, empty when none). The cache state comes from `prefetch-models --check` run off the event loop (bounded, remembered until a download ends) | none |
| `Download` | method | `() -> s` | Start the download of the missing models and return `DOWNLOADING` at once. Uses the keyring token, else the ambient login (section 6). Allowed while a recording or processing job runs (no GPU, no lease) | `MODELS_DOWNLOAD_ACTIVE` |
| `CancelDownload` | method | `() -> b` | `true` when one was running; the outcome arrives as `ModelsFinished` with `CANCELLED` | none |
| `ModelsProgress` | signal | `(sii)` | `(model, step, total_steps)`, `model` is `pipeline` or `whisper`; at most one per second | n/a |
| `ModelsFinished` | signal | `(ss)` | `(state, error_code)`, `state` `READY`, `FAILED` or `CANCELLED`, sent after the result is recorded | n/a |
| `ActiveDownload` | property | `b` (read) | True while a download runs; `PropertiesChanged` on change | n/a |

Messages carry codes and next steps, never a token, a path under HOME or child output. The download timeout is the new per-domain setting `MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS` (default 3600, replacing the installer's `MEETING_SCRIBE_PREFETCH_TIMEOUT`), added to `settings.py`, `.env.example` and `ARCHITECTURE.md` section 7. The new package `meeting_scribe.model_setup` gets the usual layering and independence import contracts (a peer of `preferences`; only `main.py` wires it).

A client that gets `UnknownInterface` treats the service as too old (the same rule `Settings1` uses): after an update the window asks to restart the service.

### 8. New error codes (follow `error-codes-catalog.md`; all register there in the same commit)

| Class | `error_code` | Meaning | D-Bus error name |
|---|---|---|---|
| `ModelsDownloadActiveError` | `MODELS_DOWNLOAD_ACTIVE` | `model_setup`: `Download` while one is running | `dev.meetingscribe.Error.MODELS_DOWNLOAD_ACTIVE` |
| `ModelsDownloadFailedError` | `MODELS_DOWNLOAD_FAILED` | `model_setup`: the download process failed for a reason not covered by `DIARIZATION_MODEL_ACCESS_DENIED` or `MODEL_UNAVAILABLE`; carried in `ModelsFinished` and `GetStatus.failure_code` | not raised to a client |
| `ModelsDownloadTimeoutError` | `MODELS_DOWNLOAD_TIMEOUT` | `model_setup`: the download exceeded `MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS` | not raised to a client |
| `ModelsDownloadCancelledError` | `MODELS_DOWNLOAD_CANCELLED` | `model_setup`: cancelled by the user; internal, reported as the `CANCELLED` state | not exposed |
| `ModelsTokenKeyringLockedError` | `MODELS_TOKEN_KEYRING_LOCKED` | `model_setup`: the keyring is locked, so the token cannot be read; the message says to unlock it; the service never prompts | not raised to a client |

Existing codes reused: `DIARIZATION_MODEL_ACCESS_DENIED` (terms not accepted, invalid token), `MODEL_UNAVAILABLE` (offline, unknown name). The preflight has no error codes: its contract is the check ids, the statuses and the exit codes above (a shell script, not a service).

### 9. Update and uninstall

**Update** is `git pull` then `./install.sh`. It is idempotent: the preflight runs again (it reports `OK` for what is there), a new `versions/<id>/` is built, `current` switches atomically, the service restarts when idle, the old version is pruned only after the new one runs, and nothing is asked. It keeps `~/Documents/meetings` (or the configured folder), `settings.json`, `.env`, the keyring items and the Hugging Face cache, because none of them live under the install root. Running it twice in a row is safe; the second run only rebuilds the same-version tree (a fresh id) and prunes the previous one.

**Uninstall** (`./uninstall.sh`, same wrapper pattern) removes what install created and prints what it keeps: meetings, `settings.json`, `.env`, the keyring items (cloud API key and Hugging Face token; remove them in Seahorse) and the downloaded models (`~/.cache/huggingface`, shared with other tools). Its safety rules (recording refusal, `FORCE=1`) are unchanged.

### 10. ADRs

Two ADRs are needed and are written with this spec as drafts (status `proposed`; the owner approves them with the spec, then they become `active`):

- [ADR-010](../decisions/010-install-never-uses-sudo.md): the install runs a read-only preflight first, never runs `sudo`, never prompts, and prints the commands. Human-only steps are labelled apart.
- [ADR-011](../decisions/011-hugging-face-token-in-keyring-and-models-from-preferences.md): the Hugging Face token lives in the GNOME Keyring, the models are downloaded by the service from Preferences with the token passed on stdin, and processing loads the cache with no token.

ADR table in `docs/README.md` and the "ADR 001-0NN" range in `AGENTS.md` are updated in the same commit (the drift tests check both).

### 11. Owner scope added 2026-10-12: preparing the repository to become public

Planned only. It runs with the README task (F6.10 in `odd/tasks/meeting-scribe.md`, after F6.9) and the repository stays private until it is done.

- **(a) License.** Add the `LICENSE` file (GPL-2.0-or-later) and declare it in `pyproject.toml` (`license` and the matching classifier) and, if the format allows it, in the extension `metadata.json`. The owner chose **GPL-2.0-or-later** (decided 2026-10-12, not an open question); the `LICENSE` file carries the GPL-2.0 text and the files declare "or later".
- **(b) Author email and history.** Change the author email in `pyproject.toml` to `richard.romero@hotmail.cl`. Then rewrite the previous work email, in every commit author and committer field, out of the whole history (a history rewrite with a mailmap-based filter). This requires a force-push of `main` and of every branch; **the owner confirms right before the force-push**, and nothing is pushed without that confirmation. Open pull requests and clones have to be recreated afterwards. The repo stays private until this is done.
- **(c) Private repository references.** Remove the references to the private repositories `qforest-chatbot` and `donde-compro` (today: `odd/tasks/meeting-scribe.md`, the "Accepted deviations from HANDOFF" section, three lines, and `docs/_templates/README.md` line 4), keeping the facts that still matter (for example that the templates are English and the per-domain settings convention is new here).

None of these steps is performed by the F6.0 documentation task.

#### Future note: extensions.gnome.org

Distribution through extensions.gnome.org is deferred to a later phase. Facts from the extensions.gnome.org review guidelines (gjs.guide), as stated by the owner and not re-verified in this task: an extension must not include binaries; privileged subprocesses are avoided and only allowed through `pkexec`; pip installs are allowed only on explicit user action. A later extension-only package could therefore not ship the service, the ML environment or an installer, and could not run a privileged install. It could reuse this preflight (`./install.sh --check --json`) as the "is this machine ready" step the user runs on their own, and the extension would point to it. That is a non-goal for F6.

### 12. Task breakdown

About 400 authored changed lines per task (advisory, counting additions plus deletions, tests and docs included). Each task is one work-unit PR into `feat/install`, with its tests and docs in the same commit.

| Task | Content | Main files | Proves |
|---|---|---|---|
| F6.1 | Root `install.sh` and `uninstall.sh` wrappers, `make install` and `make uninstall` aliases with `ARGS`, option parsing (`--check`, `--json`, `--help`), exit-code contract, report formatter (text and JSON), `scripts/preflight.sh` skeleton with the status machinery | `install.sh`, `uninstall.sh`, `scripts/preflight.sh`, `scripts/install.sh`, `Makefile`, `tests/unit/test_preflight.py`, `tests/unit/test_install_scripts.py` | AC-1, AC-4, AC-5, AC-6, AC-13 |
| F6.2 | System checks: session, shell version from metadata, bus, tools, GTK stack; README prerequisites table and its drift test | `scripts/preflight.sh`, `README.md`, `tests/unit/test_preflight.py`, `tests/unit/test_readme_prerequisites.py` | AC-3, AC-7 (HUMAN session items), AC-10, AC-11, AC-14 |
| F6.3 | Hardware and services: `nvidia-smi`, VRAM, disk per filesystem, Ollama installed, reachable, model; zero-change stop wired into `install.sh`; the install summary line `models: ready / PENDING` replaces the download step (and its timeout knob) | `scripts/preflight.sh`, `scripts/install.sh`, `tests/unit/test_preflight.py`, `tests/unit/test_install_scripts.py` | AC-2, AC-7, AC-8, AC-9, AC-12, AC-20 |
| F6.4 | Token-free processing: `load_pipeline` without a token, `fetch_pipeline(token)`, `prefetch-models --check --token-stdin --progress-json`, `python -m meeting_scribe.cli`, new error texts, offline proof | `pyannote_backend.py`, `prefetch.py`, `cli.py`, `pyannote_diarizer.py`, `tests/unit/test_prefetch.py`, `tests/unit/speakers/test_pyannote_backend.py` | AC-15 |
| F6.5 | `model_setup` domain and `Models1`: status, download runner (token on stdin), cancel, signals, shared-kernel Secret Service reader (the summaries store reuses it, no behaviour change), settings, error codes, api-contracts and catalog, import contracts, e2e on a private bus | `src/meeting_scribe/model_setup/`, `src/meeting_scribe/base/infrastructure/`, `main.py`, `settings.py`, `pyproject.toml`, `docs/architecture/*` | AC-16, AC-17, AC-18 |
| F6.6 | Review app core: Hugging Face token key store (libsecret, attributes above), `Models1` gateway and parsers, models presenter | `review-app/src/meeting_scribe_review/core/`, `review-app/src/meeting_scribe_review/ui/key_store.py`, `tests/unit/review_app/` | AC-19 (logic) |
| F6.7 | Review app UI: "Speech models" group in Preferences (token field, terms link, status, Download models, progress, cancel) | `review-app/src/meeting_scribe_review/ui/preferences_dialog.py`, `ui/` | AC-19 (UI) |
| F6.8 | Update and uninstall: idempotent re-run proof, uninstall keep-list, existing `hf auth login` still downloads | `scripts/install.sh`, `scripts/uninstall.sh`, `tests/unit/test_install_scripts.py` | AC-21, AC-22, AC-17 (login path) |
| F6.9 | `docs/runbooks/install.md` (install, update, troubleshoot), README section "Install (humans and AI agents)", the other runbooks, `ARCHITECTURE.md`, ADR-010 and ADR-011 to `active`, docs gate, validation with consent on the real machine (no recording, no audio) | docs, README | docs gate, manual check of section 6 |
| F6.10 | Prepare the repo to become public (section 11): license, author email and history rewrite, private repository references | `LICENSE`, `pyproject.toml`, `gnome-extension/.../metadata.json`, `odd/`, `docs/_templates/README.md` | owner review; the force-push needs the owner's confirmation |

---

## Definition of Done

- [ ] All acceptance criteria pass in `make check`
- [ ] D-Bus interface documented in `architecture/api-contracts.md`; new error codes in `architecture/error-codes-catalog.md`
- [ ] Unit tests written first (RED), then implementation (GREEN)
- [ ] Lint, format, type check and import contracts clean
- [ ] Traceability updated in `project/mvp-scope.md` (FR-7 row)
- [ ] `docs/runbooks/install.md`, the README prerequisites and the "Install (humans and AI agents)" section written
- [ ] ADR-010 and ADR-011 `active` after the owner approves them
- [ ] New rules or gotchas added to `project/rules-registry.md` (GO-047 is superseded by this phase)
- [ ] `.env.example` and `ARCHITECTURE.md` section 7 list `MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS`
- [ ] Docs gate for the tracker PR (audit, language scan)

---

## Decisions taken during implementation

> If unresolved forks appear, record them here AND open an ADR if the decision is structural.

- 2026-10-12: root `install.sh` is a thin wrapper over `scripts/install.sh` (not a move), because the tests, docs and `_common.sh` paths all address `scripts/` and the wrapper gives the single entry point at no risk.
- 2026-10-12: one machine-readable format, `--json`, plus a stable tagged human format, instead of a second line protocol.
- 2026-10-12: the keyring token wins over the ambient login, with the login as a fall back only when no keyring token exists.
- 2026-10-12: the service (not the app) downloads, so the token never crosses D-Bus and both clients stay thin.
- 2026-10-12: the owner chose GPL-2.0-or-later for the public repository (the `LICENSE` file and metadata are added in F6.10, not before).
- 2026-10-12: a missing default Ollama model stops the install with `ollama pull <model>` as the fix, consistent with "nothing missing".
