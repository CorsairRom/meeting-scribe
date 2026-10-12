---
type: registry
status: living  # ALWAYS 'living' — this doc is never "finished"
last_updated: 2026-10-11
update_frequency: on-every-decision
ai_instructions:
  - "This is the PROJECT LOGBOOK. Rules accumulate, they are not replaced."
  - "BEFORE writing code, read the active rules of the category you are about to touch."
  - "AFTER making a decision (with or without the human), add the rule here BEFORE closing the session."
  - "Rules have an immutable ID (BR-XXX, CR-XXX, GO-XXX, AP-XXX). If a rule is invalidated, mark it 'deprecated' but do NOT delete it."
  - "Each rule needs: id, date, context, rule, source, status."
  - "If a new rule contradicts an active one, mark the old one 'superseded' and create the new one with the next ID."
---

# Living Rules Registry

> Institutional memory of the project. Updated **whenever a decision is made**, whether structural
> (ADR) or tactical (minor rule). See [`doc-sync-protocol.md`](doc-sync-protocol.md).

## How to use this registry

- `BR-XXX` Business Rule · `CR-XXX` Code Rule · `GO-XXX` Gotcha · `AP-XXX` Anti-Pattern.
- IDs are **immutable**; never reuse one, even if the rule is deprecated.
- Status: `active` (in force) · `superseded` (replaced, link it) · `deprecated` (no longer applies).
- Add a rule for: behavior decisions, conventions, discovered gotchas, explicit prohibitions, changed defaults.
- Do NOT add: trivial rules, anything an ADR already covers (link it instead), personal preferences with no technical impact.

---

## Business Rules (BR)

### BR-001 — Never start a recording or play audio without explicit user action
- **Date**: 2026-10-09
- **Status**: active
- **Context**: In this project's history a test played speech into the user's headset and recorded a live meeting.
- **Rule**: Recording starts only from an explicit user action (panel button or an explicit D-Bus call). Nothing plays audio unasked. Automated tests never touch audio devices.
- **Source**: HANDOFF section 3; PRD safety requirements
- **Verifiable in**: `tests/` (no audio-device access; see AP-003)

### BR-002 — Meeting audio and transcripts are private and stay under the meetings directory
- **Date**: 2026-10-09
- **Status**: active
- **Context**: Recordings contain private conversations.
- **Rule**: Audio, transcripts, voice samples and summaries are stored only under the configured meetings directory (`STORAGE_MEETINGS_DIR`). They never enter the repository, test fixtures, docs or logs.
- **Source**: HANDOFF section 3; ADR-004
- **Verifiable in**: `.gitignore` (audio and cache patterns); `src/meeting_scribe/settings.py::StorageSettings`

### BR-003 — A cloud LLM provider is opt-in and labelled "cloud"
- **Date**: 2026-10-09
- **Status**: active
- **Context**: The default is fully local (Ollama). A cloud provider sends transcript text off the machine.
- **Rule**: Cloud providers are disabled until explicitly enabled in settings, visibly labelled "cloud" in the UI, and confirmed per request.
- **Source**: HANDOFF section 3; ADR-006

### BR-004 — The transcript leaves the machine only on explicit user action
- **Date**: 2026-10-09
- **Status**: active
- **Context**: Local-first product; the single exit path is the summary request.
- **Rule**: A transcript is sent to a provider only when the user explicitly requests a summary and the provider is configured. No background uploads, no telemetry.
- **Source**: HANDOFF section 3; ADR-006

### BR-005 — The cloud API key lives in the GNOME Keyring and nowhere else
- **Date**: 2026-10-11
- **Status**: active
- **Context**: The cloud key is a paid credential; settings and `.env` files get copied, backed up and read by tools.
- **Rule**: The OpenCode Go key is read on demand from the freedesktop Secret Service (attributes `application=meeting-scribe`, `key=opencode-api-key`) when a confirmed cloud summary starts, and held in memory as a `SecretStr` for that request only. It is never logged, never in an error message, `meeting.json`, a D-Bus reply, a command line or a worker environment, and it goes only over HTTPS to the configured base URL. A locked keyring or a missing key fails with `SUMMARY_CLOUD_KEY_LOCKED` or `SUMMARY_CLOUD_KEY_MISSING`; the service never prompts. The same holds for the Preferences feature: the key is not a field of `Settings1`, is never in `settings.json` and is never sent over D-Bus (the review app stores it in libsecret itself). Honest limitation: while the session is unlocked any process running as the user can query the keyring.
- **Source**: ADR-009; spec `summaries.md` section 8a; spec `preferences.md` (AC-9)
- **Verifiable in**: `tests/unit/preferences/test_no_secret_in_settings.py`, `tests/e2e/summaries/test_secret_service_store.py`, `tests/unit/summaries/test_secret_service_message.py`, `tests/unit/summaries/test_openai_adapter.py::test_key_only_in_the_header`

---

## Code Rules (CR)

### CR-001 — Domains are independent of each other
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: `recording`, `transcription`, `speakers` and `summaries` never import each other.
- **Source**: HANDOFF section 4; ARCHITECTURE.md
- **Verifiable in**: import-linter contract "Domains recording, transcription, speakers and summaries are independent" (`make imports`)

### CR-002 — The domain layer has no framework imports
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: `domain/` imports no `pydantic_settings`, `dbus_fast` or `gi`, and no `application`/`infrastructure` code of its own package.
- **Verifiable in**: import-linter contracts "Domain layers do not import frameworks" and "Layers per package"

### CR-003 — Only `meetings` depends on other domains, and only via `application`
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: `meetings` orchestrates; it may import another domain's `application` layer, never its `domain` or `infrastructure`.
- **Verifiable in**: import-linter contract "meetings only depends on other domains through their application layer"

### CR-004 — Controllers return `returns.Result`, never raise
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: Application controllers return `Result[T, E]` where `E` is a `MeetingScribeError`. D-Bus interface adapters map the `Failure` to a `dev.meetingscribe.Error.<ERROR_CODE>` error.
- **Source**: HANDOFF section 4; [`error-codes-catalog.md`](../architecture/error-codes-catalog.md)

### CR-005 — Repositories return null objects, not `None`
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: A missing entity is a null object: a subclass setting the explicit `_is_null_object: ClassVar[bool] = True` marker on `BaseEntity` (conventionally named `_Null<Entity>`, but the name is not what makes it null); callers convert it with `BaseEntity.as_result(error)`.
- **Verifiable in**: `src/meeting_scribe/base/domain/entity.py::BaseEntity`; `tests/unit/base/test_entity.py`

### CR-006 — State transitions are entity methods returning `model_copy`
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: Entities are frozen. A transition is a method that validates and returns `self.model_copy(update=...)` (or a `Failure` with `InvalidStateTransitionError`). Pass `now` in; do not read the clock inside the entity.
- **Verifiable in**: `src/meeting_scribe/base/domain/entity.py` (`frozen=True`)

### CR-007 — Settings are injected from the composition root
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: Domains never import `meeting_scribe.settings` or `meeting_scribe.main`. `main.create_app(settings)` builds adapters from settings and passes them in. Per-domain settings classes use `env_prefix`.
- **Verifiable in**: import-linter contract "Domain packages do not import settings or the composition root"

### CR-008 — Absolute imports only
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: `from meeting_scribe.x.y import z`; never relative imports.
- **Verifiable in**: ruff (`ruff.toml`)

### CR-009 — Tests use fakes over mocks and synthetic WAV fixtures only
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: Tests prefer in-memory fakes of ports over mock libraries. Audio fixtures are generated synthetic WAVs (tones/silence), never real recordings. Tests live in `tests/{unit,e2e}/<domain>/`.
- **Source**: HANDOFF sections 3 and 4

### CR-010 — Conventional Commits without AI attribution
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: `<type>(<scope>): <description>`, lowercase, imperative. No `Co-Authored-By`, no AI attribution lines.
- **Source**: user global rule

### CR-011 — D-Bus names and error mapping
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: The bus name is `dev.meetingscribe.Service`, the object path `/dev/meetingscribe/Service`, interfaces are `dev.meetingscribe.<Name>1`, and domain errors are raised as `DBusError("dev.meetingscribe.Error.<ERROR_CODE>", message)`. Names are renamable until F6.
- **Source**: [ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md); [`error-codes-catalog.md`](../architecture/error-codes-catalog.md)
- **Verifiable in**: `docs/architecture/api-contracts.md`; `tests/e2e/recording/` (private `dbus-daemon`)

### CR-012 — Extension logic lives in pure modules under `lib/`; Shell glue stays thin
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: Presentation logic of the GNOME extension lives in ES modules under `gnome-extension/meeting-scribe@meetingscribe.dev/lib/` with no `gi://` or `resource:///org/gnome/shell/` imports, so it runs under plain `gjs` and is unit-tested. Strings are wrapped with an injected `_` (identity by default), never imported from the Shell. `extension.js` and the D-Bus client only wire these modules.
- **Source**: [ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md); F2.1
- **Verifiable in**: `gnome-extension/tests/` (`make test-extension`)

### CR-013 — The panel button intercepts `menu.toggle()`, not the click event
- **Date**: 2026-10-09
- **Status**: active
- **Rule**: In GNOME Shell 50 `PanelMenu.Button` opens its menu from a `Clutter.ClickGesture` (`recognize` calls `this.menu?.toggle()`), so overriding `vfunc_event` or `button-press-event` no longer controls it. The indicator passes `dontCreateMenu = true` and installs its own `PopupMenu` subclass through `setMenu()` whose `toggle()` calls the controller's `onPrimaryClick()` and only calls `super.toggle()` for `open-menu`. Since the 2026-10-09 decision the controller only returns `open-menu`/`close-menu`, so the hook always falls through to `super.toggle()`; it is kept on purpose as the single click decision point (nobody should look for a click-to-start path, and any future click action must go through it). Every state opens the menu on click (idle/stopped offer Start, recording offers Stop): a stray click on the top bar must never record (user decision 2026-10-09, replacing the earlier one-click start).
- **Source**: gnome-shell `gnome-50` `js/ui/panelMenu.js` and `popupMenu.js`; [ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md); F2.3
- **Verifiable in**: `gnome-extension/meeting-scribe@meetingscribe.dev/lib/panelIndicator.js` (needs a manual check in a real shell, F2.4); controller behaviour in `gnome-extension/tests/indicatorController.test.js`

---

## Gotchas (GO)

### GO-001 — Preload pip CUDA libs before importing faster-whisper
- **Date**: 2026-10-09
- **Status**: active
- **Symptom**: CTranslate2 cannot find cuBLAS/cuDNN.
- **Cause**: `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` live in `site-packages/nvidia/*/lib` and are not on `LD_LIBRARY_PATH`.
- **Solution**: `ctypes.CDLL` each `.so` before importing `faster_whisper`. Log failures; do not swallow `OSError` silently (prototype did).
- **Implemented (F3.2)**: `transcription/infrastructure/cuda_preload.py` loads every `nvidia/*/lib/*.so*` with `RTLD_GLOBAL` in a stable order, logs each failure at WARNING with the library name and a summary at INFO; it runs only for `device == cuda`, before `ctranslate2` or `faster_whisper` is imported. On this machine all 17 libraries load.

### GO-002 — torch cu130 and ctranslate2 cu12 coexist
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `pyannote.audio` pulls torch `+cu130`, which supports Blackwell (sm_120). CTranslate2 uses its own cu12 libs (GO-001). Both work in one process; do not try to unify them.

### GO-003 — faster-whisper 1.2.1 with PyAV 19 is broken
- **Date**: 2026-10-09
- **Status**: active
- **Symptom**: `TypeError` from `av.open(..., metadata_errors=...)`.
- **Solution**: decode our own 16 kHz s16 WAV chunks with stdlib `wave` + numpy and pass a float32 array to `model.transcribe` (see GO-039 for the reader). `av` 19.0.1 is installed with faster-whisper but never used.

### GO-004 — pyannote models are gated; never print the HF token
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the community diarization model needs a free Hugging Face account, accepted terms and a login. The token lives in the Hugging Face cache; rely on the ambient login, never read, log or print the token.

### GO-005 — sherpa-onnx was rejected
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: its pip build is CPU-only and over-clustered one meeting into ~210 speakers. Do not revisit without new evidence.

### GO-006 — Background processes ignore SIGINT
- **Date**: 2026-10-09
- **Status**: active
- **Symptom**: a service or ffmpeg started with `&` does not react to Ctrl+C.
- **Solution**: install explicit SIGINT/SIGTERM handlers; start ffmpeg with `start_new_session=True`; stop it by writing `q` to its stdin; add a kill fallback after a timeout (the prototype had none).

### GO-007 — `pkill -f` can match your own shell
- **Date**: 2026-10-09
- **Status**: active
- **Solution**: kill by PID, never by pattern.

### GO-008 — The `!` prefix of the agent CLI is non-interactive
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: interactive logins (for example `hf auth login`) must run in a real terminal.

### GO-009 — A mono mix loses who-is-who
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: mixing mic and system audio into mono prevents attributing speech to the local user. See [ADR-002](../decisions/002-separate-mic-and-system-audio-tracks.md).

### GO-010 — Prototype: models are never freed from the GPU
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: Whisper and pyannote stayed resident together on an 8 GB card. Product rule: lazy load, release between stages ([ADR-003](../decisions/003-process-after-stop-single-gpu-worker.md)).

### GO-011 — Prototype: silent CUDA preload and over-broad CPU fallback
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: preload errors were swallowed and any exception triggered the CPU fallback. Product rule: log preload failures; fall back only on genuine CUDA unavailability, and say so.

### GO-012 — Prototype: cache ignores changed parameters
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `words.json` was reused even when model, language or VAD settings changed. Cache keys must include processing params ([ADR-004](../decisions/004-folder-per-meeting-json-storage.md)).

### GO-013 — Prototype: chunk concatenation assumes exact 30 s multiples
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: timing offsets were computed as `index * 30 s`, and the whole session was held in RAM as float32 (~230 MB per hour). Use real chunk durations and stream or window the audio.

### GO-014 — The review app runs on system python; the service on the venv
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the project venv is uv Python 3.12 (required by torch, ctranslate2 and pyannote), where the system `gi` is not importable. The GTK4 review app runs with `/usr/bin/python3` and system PyGObject (Fedora 44: python3-gobject 3.56.3, gtk4 4.22.4, libadwaita 1.9.3). Keep presenters free of `gi` so pytest in the venv can test them. Building PyGObject into the venv needs `gobject-introspection-devel` and `cairo-gobject-devel`; revisit only if the split causes friction ([ADR-005](../decisions/005-review-ui-native-gtk-app.md)).

### GO-015 — Session folder creation must fail on an existing folder
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `audio_dir.mkdir(parents=True, exist_ok=False)` alone succeeds when the session folder already exists without `audio/`, so a second recording started in the same second could reuse a folder. `FilesystemRecordingStorage.prepare` creates the session directory with `exist_ok=False` first, then `audio/`. Session ids have one-second resolution (`YYYY-MM-DD_HHMMSS`).

### GO-016 — lavfi test sources need `-re` and a hard `-t` cap
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: ffmpeg `-f lavfi` sources ignore the wall clock: without `-re`, 60 s of audio was written in 0.08 s (measured), and an uncapped source would fill the disk. Integration tests pass `-re` (real-time pacing) and `-t 15` as a safety net, write only under `tmp_path`, and reap every process by PID. Never use `-f pulse`/`alsa` in tests (BR-001, AP-003).

### GO-017 — A clean `q` stop exits with code 0 and finalizes the last chunk
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: measured with ffmpeg 8.1.2: `q` on stdin makes ffmpeg exit with code 0 in about 0.1 s, and the in-progress WAV is closed with a valid header (the last chunk is just shorter, a few frames if stop lands right after a boundary). `FfmpegAudioCapture.stop` therefore treats any non-zero code as failure. stdin must stay open, so `-nostdin` must never be added to the command.

### GO-018 — Segment chunks are slightly longer than `chunk_seconds` and are final only once the next starts
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the segment muxer cuts on packet boundaries (1 s chunks measured at 16026-16043 frames at 16 kHz, not exactly 16000), so never compute timing as `index * chunk_seconds` (see GO-013); read real durations. The muxer finalizes a chunk when it starts the next one or when ffmpeg stops (not verified mid-recording), so while recording treat only chunks below the highest index as complete; tests read chunks after `stop`.

### GO-019 — `amerge` keeps input order: the microphone must be input 0
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: channel 0 = first `-i` (microphone), channel 1 = second (system monitor), per ADR-002. Verified with synthetic sines and silence, swapping the inputs. Each input is forced to mono first (`aformat=channel_layouts=mono`), otherwise a stereo monitor would add channels and shift the mapping.

### GO-020 — Capture stop must stay bounded and say what it did
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the service runs on one D-Bus event loop (F1.4), so an unbounded wait wedges everything. `FfmpegAudioCapture.stop` bounds every wait (`q` timeout, SIGTERM grace, SIGKILL grace; worst case 14 s with defaults), reports "terminated" vs "killed" truthfully, and returns "could not be reaped" (keeping the handle for a retry) if the process survives SIGKILL, for example in uninterruptible I/O on a hung mount.

### GO-021 — Surface the ffmpeg exit cause, not a generic crash message
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `AudioCapture.exit_detail()` returns the exit code and `ffmpeg.log` tail of a dead process. `RecordingController` uses it for `failure_reason` and the returned error (status/start/stop crash paths), falling back to `CAPTURE_CRASHED_REASON`. ffmpeg can also die after the startup grace; that shows up as `FAILED` on the next call. Opening `ffmpeg.log` and spawning ffmpeg have separate error handling, so a missing session folder is not reported as "ffmpeg not found".

### GO-022 — dbus-fast `@dbus_method` wrappers swallow the return value in Python
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: calling a decorated method directly (`interface.Start()`) runs it and returns `None` (an `async` one is never awaited), because the decorator wraps the function for the bus and registers the original separately (dbus-fast 5.2.0, `service.py`). Keep behavior in plain methods (`start`/`stop`) and make the decorated member a one-line delegate. `dbus_property` getters do work as ordinary Python properties. Annotations must not use `type` statement aliases (parse_annotation rejects them): annotate with `dbus_fast.annotations` types or `Annotated[..., DBusSignature(...)]` assigned to a plain name.

### GO-023 — Request the bus name with `DO_NOT_QUEUE` and check the reply
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `request_name(name)` with default flags queues behind the current owner and returns `IN_QUEUE`, so a second instance would sit silent. `serve` passes `NameFlag.DO_NOT_QUEUE` and exits with code 1 unless the reply is `PRIMARY_OWNER`.

### GO-024 — The controller is not thread-safe: serialize calls and never read it mid-call
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `RecordingInterface` runs each controller call in `asyncio.to_thread` under one `asyncio.Lock`. While a call is in flight, property reads and the 2 s crash monitor use the last published snapshot instead of calling `status()` from the loop thread. Shutdown (`RecordingInterface.shutdown`) asks the monitor to finish its current iteration (never cancel it mid-`to_thread`: the worker thread would keep running), then `stop_if_active` takes the same lock and stops an active recording because ffmpeg runs in its own session and would otherwise be orphaned. A failed stop is returned, logged at ERROR with its code, and makes `serve` exit 2. The monitor logs a failing poll and keeps polling.

### GO-025 — Private-bus tests: explicit address, own config, kill by PID
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the `bus_address` fixture (`tests/e2e/conftest.py`) runs `dbus-daemon --config-file=<temp> --nofork --print-address=1` and clients pass that address to `MessageBus(bus_address=...)`; `DBUS_SESSION_BUS_ADDRESS` is never read, so the user's bus cannot be reached by accident. The temp config has no service directories, so nothing is activated. Every app is wired with the fakes: `Start` on real adapters records the microphone.

### GO-026 — `serve` races the stop signal against bus disconnect and has defined exit codes
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `serve` waits for `stop` or `bus.wait_for_disconnect()` (dbus-fast 5.2.0 raises or resolves when the connection closes). A lost bus is logged at ERROR, the active recording is stopped, and the exit code is 3. Exit codes: 0 clean, 1 name taken, 2 shutdown stop failed, 3 bus disconnected, 4 the processing loop outlived the shutdown bound (`SHUTDOWN_TIMEOUT_SECONDS`; a worker process may be left over, see the ERROR log). Precedence when several apply: 3, then 2, then 4. Only exit 1 is excluded from `Restart=on-failure`. After exit 4 systemd restarts the unit and first kills a leftover worker: with `KillMode=mixed`, once the main process has exited, SIGKILL goes to all remaining processes of the unit's control group (`man systemd.kill`, KillMode=: "the main process of a unit has exited (applies to KillMode=: mixed)"), so a stray worker does not survive into the next start. `_publish` catches emit errors so a dead bus cannot abort shutdown.

### GO-027 — Run extension tests as ES modules: `gjs -m`, explicit imports, `System.exit`
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `gjs -m <file>` runs the entry file as a module (ESM specifiers need the `.js` extension and relative paths). gjs has no directory discovery, so `gnome-extension/tests/run.js` imports each `*.test.js` explicitly (add new files there). A failing run must end with `System.exit(1)` (`import System from 'system'`); an uncaught exception also exits 1 but only logs `Gjs-CRITICAL`. The uuid directory contains `@`, which is valid in a relative import specifier.

### GO-028 — GJS D-Bus proxies: unannounced properties go stale, and error names must be read before stripping
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: verified against GJS 1.88.1 with a private bus. (1) A `Gio.DBusProxy` property getter reads the proxy cache, which only refreshes on `PropertiesChanged`; a property the service never announces (`ElapsedSeconds`) stays at its construction-time value, so read it with an explicit `org.freedesktop.DBus.Properties.Get` and count locally afterwards (`recordingClient.js`). (2) Proxy-wrapper methods have `<Name>Async()` promise variants that resolve with the unpacked out args array. (3) On a failed call, read `Gio.DBusError.get_remote_error(e)` first: `strip_remote_error(e)` rewrites `e.message` in place. (4) `connectSignal` ids are released with `proxy.disconnectSignal(id)`, `g-properties-changed` ids with `proxy.disconnect(id)`. (5) Watch a name with `Gio.bus_watch_name_on_connection(..., Gio.BusNameWatcherFlags.NONE, ...)` and create the proxy with `Gio.DBusProxyFlags.DO_NOT_AUTO_START` so merely watching never launches the service. Sources: gjs.guide D-Bus guide (Context7) plus the tests in `gnome-extension/tests/recordingClient.test.js`.

### GO-029 — Extension D-Bus tests run on a private daemon and must always clean up
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `gnome-extension/tests/privateBus.js` starts `dbus-daemon --config-file=<temp> --nofork --print-address=1` (no service directories, like GO-025) via `Gio.Subprocess` and connects with `Gio.DBusConnection.new_for_address`; the session bus is never used. The harness timeout rejects a test but cannot cancel it, so a body that waits forever would leak the daemon: every wait inside a fixture (daemon address, connect, name acquisition in `FakeRecordingService.appear()` which also rejects on name-lost, daemon exit, close) is bounded by `withTimeout` at 2 s, and client calls in tests use a short `callTimeoutMs`, so `finally` runs `bus.stop()`. `stop()` is idempotent, force-exits the daemon by PID first and removes the temp dir including the socket the killed daemon leaves behind. As a backstop, `PrivateBus.start()` registers `stop()` with the harness (`registerCleanup`); the harness runs registered cleanups after every test, even a timed-out one, and `run.js` runs them again before `System.exit`. Production `RecordingClient` calls default to a 20 s timeout (`Stop` blocks up to `RECORDING_STOP_TIMEOUT_SECONDS` + 4 s in the service). A leaked daemon also keeps a piped stdout open, so run the suite with output redirected to a file when debugging. Check with `pgrep -a dbus-daemon` after a run.

### GO-030 — Extension lifecycle: create everything in `enable()`, release everything in `disable()` (EGO)
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: GNOME Extensions review rules (gjs.guide review guidelines) forbid work in the constructor or at module scope and require that `disable()` leaves nothing behind. `extension.js` only imports at module scope; `enable()` creates the scheduler, indicator, `RecordingClient` (session bus by default) and controller, and `disable()` destroys them in reverse order and nulls the references. Timers come from `GLibScheduler` (`GLib.timeout_add_seconds`), which tracks every source and calls `GLib.source_remove` on cancel and destroy; the client unwatches its bus name and disconnects its proxy handlers. Signal callbacks passed into the client and indicator dereference `this._controller` lazily with `?.`, so a late callback after `disable()` is a no-op. Errors are shown with `Main.notify(title, body)` (gnome-50 `main.js`: a transient notification on `MessageTray.getSystemSource()`). `metadata.json` declares `uuid`, `name`, `description`, `shell-version: ["50"]`, `gettext-domain` and `url`; translation uses `this.gettext()` from `Extension`. No synchronous I/O runs in the shell: all D-Bus calls are async.

### GO-031 — Shell-only files are checked statically, never executed by tests
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `extension.js` and `lib/panelIndicator.js` import `resource:///org/gnome/shell/...`, which plain `gjs` cannot load. `gnome-extension/tests/shellGlue.test.js` parses them with `node --check --input-type=module -` (source piped through stdin, so no scratch file; `node` is required to run `make test-extension`), checks that relative imports exist and that other imports are `gi://` or Shell modules, keeps `lib/indicatorModel.js` and `lib/indicatorController.js` free of `gi://` and Shell imports (CR-012), and validates `metadata.json`. The same test feeds deliberately broken source to the checker to prove it fails on a syntax error. This catches syntax and wiring mistakes only; Shell API use is verified manually in F2.4.

### GO-032 — `meeting.json` round-trips unknown keys through `extra="allow"`, and an entity needs its folder
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: The spec requires unknown keys to survive a rewrite (forward compatibility). Every `meetings` model (the `Meeting` entity and its nested sections) sets pydantic `extra="allow"`, which keeps unknown keys through `model_validate`, `model_copy` and `model_dump`; a plain model would silently drop them on the first save. `Meeting.directory` is `Field(exclude=True)`: it is derived from the folder the repository read, never serialized. The schema check runs before validation (newer `schema_version` -> `MEETING_SCHEMA_UNSUPPORTED`, listed as `UNSUPPORTED` and never rewritten; missing, non-int or id-mismatched -> corrupt, skipped by list). `get` rejects ids that are not a bare folder name (path traversal), returning the null object. The atomic write uses a temp file in the same folder (`mkstemp`), `fsync`, then `Path.replace` (ruff PTH105 forbids `os.replace`; it calls it) so a crash or `OSError` never leaves a torn file, then `fsync`s the folder so the rename itself is durable (a power loss cannot revert the new state). That last `fsync` runs after the file is already visible, so an `OSError` from it (some FUSE/CIFS mounts reject directory `fsync`) is logged at WARNING and the save still succeeds. `save` refuses a meeting whose `directory` is not exactly `<root>/<id>` or whose id is not a bare folder name (`MEETING_STORAGE_FAILED`), so a `../` id can never write outside the meetings root. `WavChunkInspector` maps header truncation (`struct.error`, `EOFError`) to `MEETING_STORAGE_FAILED` like any other unreadable chunk.

### GO-033 — A worker is stopped through a per-job cancel signal, not a `terminate()` call
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `preempt()` and `shutdown()` can fire between the loop taking a job and the worker spawning, when there is no process to signal; a `terminate()` method would lose that request (or, with a sticky flag, cancel an unrelated later job). `ProcessingController` therefore creates one `threading.Event` per job under its lock, `preempt()`/`shutdown()` set it, and `WorkerRunner.run` checks it before spawning and every 50 ms while waiting. `run_next` also refuses to take a job while a recording is active, under the same lock. A cancelled run returns `WorkerCancelledError`: the job is not failed, keeps its in-progress state on disk, and goes back to the queue head (preemption) or waits for the next start (shutdown).

### GO-034 — Stopping a worker: process group by id, bounded, and the stderr tail starts at this run
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `SubprocessWorkerRunner` starts the worker with `start_new_session=True` and signals the group (`os.killpg(pid, ...)`, never by pattern, GO-007): SIGTERM, `PROCESSING_WORKER_KILL_GRACE_SECONDS`, SIGKILL, then a bounded reap. A reader thread queues stdout lines so timeout and cancellation are honoured while the worker is silent. `processing.log` is appended by every stage, so the crash reason records the file size before the spawn and reads only the bytes this run wrote. Exit rules: 0 needs a `result`, 1 with an `error` event is the worker's own failure (`WorkerReportedError` keeps the worker's code), anything else is `WORKER_CRASHED`; a bad line, an unknown event or an event after the final one is `WORKER_PROTOCOL_ERROR` and stops the worker at once. A worker that stays alive more than `result_exit_grace_seconds` (10 s) after its `result` or `error` event is terminated and that final event is kept (the result is not discarded as a timeout). If the job spec cannot be written to stdin (an `OSError` other than a broken pipe) the just-spawned worker is terminated before `WORKER_CRASHED` is returned. `kill_grace_seconds` has no default: it comes from `ProcessingSettings`.

### GO-035 — Resume restarts at the first worker, and observers hear only persisted states
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `Meeting.resume(now, params)` moves an interrupted job (`TRANSCRIBING`, `DIARIZING`, `EXTRACTING_SAMPLES`) to `TRANSCRIBING` keeping `queued_at` and `attempt`; the transcribe worker answers `cache_hit` when its cache key matches (spec 4), so a meeting stored in `DIARIZING` skips transcription without the service guessing cache validity. Nothing is rewritten at `resume()` time: the disk keeps the old in-progress state until the loop picks the job up (AC-16). `ProcessingEvents.state_changed` fires only after `save` succeeded; recording the worker's device is a silent save (same state, no signal). Observer exceptions are logged at WARNING and never change the job (AC-18).

### GO-036 — A job's end is an explicit outcome, and a save failure never strands it
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `_Job.run` returns a `JobOutcome` (`DONE`, `FAILED`, `INTERRUPTED`, `UNSAVED`, `UNLOADED`, `UNREADABLE`, `SKIPPED`), never a bool. When a state change cannot be saved (disk full), the job first tries to record a failure; if that also cannot be saved the outcome is `UNSAVED`: the disk keeps the last good state, nothing is signalled (persist-before-signal, GO-035), the job is parked and retried after a delay from `PROCESSING_STORAGE_RETRY_DELAYS_SECONDS` (2 s, 8 s, 30 s) while other jobs keep running, so at most `len(delays) + 1` runs in a row. After that the controller logs "giving up" once at ERROR, calls `ProcessingEvents.storage_failed(meeting_id, error_code)` (never the repository error text, which can quote `meeting.json` content) so a UI can show it, and drops the job from memory only: it is still unfinished on disk, so the next `resume()` picks it up. A repository read failure is `UNLOADED` (same backoff, log wording "could not be read"); a corrupt file (`MeetingCorruptError`, code `MEETING_CORRUPT`, raised by the filesystem repository for bad JSON/UTF-8, a missing or mismatched id, or an invalid model) or a newer schema (`MeetingSchemaUnsupportedError`) is `UNREADABLE`: reported through `storage_failed` and never retried. `ProcessingConfig` has no defaults at all (`params` included) and validates `gt=0`; `ProcessingSettings` is the single source and the composition root copies it. `enqueue` on a `DONE`/`FAILED` meeting reprocesses it (documented on the method; the passed recording is ignored).

### GO-037 — The worker process exit is what frees the GPU (measured)
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: measured with the opt-in GPU test (`uv run pytest -m gpu -s`, synthetic audio, RTX 5060 Laptop 8 GB, `large-v3-turbo` float16): `nvidia-smi` memory used was 457 MiB before the worker, peaked at 2516 MiB while it ran (about 2.0 GB for the model plus the CUDA context) and was back at 457 MiB after the process exited. The model is loaded about 2.5 s after the process starts and a whole stage of 11 s of audio took 4.2 s of wall time; a cache-hit run takes 0.2 s because it never imports or loads a model. The test asserts the release against the baseline with a 200 MiB tolerance (a desktop session moves the baseline by itself). Releasing the model in-process (`del`, `gc.collect()`) was not measured: ADR-007 relies on the process exit only.

### GO-038 — The ML-isolation contract counts lazy imports, so one backend module holds them all
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: import-linter (grimp) sees an `import faster_whisper` inside a function exactly like a top-level one. The contract "The service never imports ML libraries" therefore lists the one module that may (`transcription/infrastructure/faster_whisper_backend.py`) under `ignore_imports`, and a second contract forbids `meeting_scribe.main` from reaching that module or `meeting_scribe.worker` at all, so the exception cannot leak into the service through a chain. Everything else (`faster_whisper_transcriber.py`, `cuda_preload.py`, the worker) talks to the library through the `WhisperBackend` port, which also lets the tests inject a fake model. Both contracts were checked to bite with a temporary violation (BROKEN, then reverted). The pyannote adapter (F3.3b) added `speakers/infrastructure/pyannote_backend.py` (`-> torch`, `-> pyannote`) to the ignore list and to the second contract; both were checked to bite again (a `import torch` in a service module and an import of the backend from `main` each turned the contracts BROKEN, then were reverted).

### GO-039 — Decode a chunk by the timeline's frame count, with the handle `wave` leaves at the first sample
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: a chunk left by a crashed ffmpeg can carry a data size of 0 or `0xFFFFFFFF`, so `wave`'s own frame count is wrong. `WavMixReader` opens the file itself, lets `wave.open(handle)` parse the header only (it does not close a handle it did not open), reads `handle.tell()` as the data offset and then reads exactly the frames the timeline recovered from the file size. The mix is `(ch0 + ch1) / 2` scaled by `0.5 / 32768` into one preallocated float32 array (no `concatenate`); a chunk that shrank after inspection is `MEETING_STORAGE_FAILED`, never a silent zero tail.

---

## Anti-Patterns (AP)

### AP-001 — No ML inside gnome-shell
- **Date**: 2026-10-09
- **Status**: active
- **Reason**: it freezes or crashes the desktop and GJS has no ML stack. The extension is a thin D-Bus client ([ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md)).

### AP-002 — No real meeting audio in fixtures, docs or the repo
- **Date**: 2026-10-09
- **Status**: active
- **Reason**: privacy (BR-002). Use generated synthetic WAVs and placeholder text.

### AP-003 — No tests that hit audio devices
- **Date**: 2026-10-09
- **Status**: active
- **Reason**: BR-001. Capture and playback sit behind ports; tests use fakes.

### AP-004 — No browser or HTTP surface for a desktop-only tool
- **Date**: 2026-10-09
- **Status**: active
- **Reason**: a localhost HTTP server is reachable by any website in the user's browser (CSRF, DNS rebinding) and forces hardening work. Clients use the per-user D-Bus session bus and a native GTK4 app ([ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md), [ADR-005](../decisions/005-review-ui-native-gtk-app.md)).

### GO-040 — Speaker assignment is pure and keyed by plain strings; the speakers domain owns its own word type
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `speakers/domain` never imports `transcription` (the domains are independent), so it has its own `TimedWord` and the composition root (the diarize worker) maps `Word` to it. Speakers are plain string keys: pyannote labels such as `SPEAKER_03`, plus `ME`, `UNKNOWN` and `FALLBACK_SPEAKER` (used when diarization returned no turns, numbered `Speaker 1`; the prototype crashed on `max()` of an empty list). `number_speakers` numbers by first *utterance*, so a speaker that owns no word takes no number, and `ME`/`UNKNOWN` are never numbered. `SpeakerLabel.id` (`ME`, `UNKNOWN`, `S1`...) is stable and never changes with a name; only `display` does, and a blank name keeps the label. `format_timestamp` truncates (does not round) to whole seconds. `assign_speakers` sorts the turns by `(start, end)` so ties go to the earlier turn whatever the input order; it is words x turns, which is fine at meeting scale.

### GO-041 — "Me" detection and sample selection are pure Python over precomputed frame energies
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the spec's "only Me detection needs numpy" is met on the worker side: the diarize worker (F3.3b) computes the two 100 ms mean-square envelopes from the audio and passes them as `Sequence[float]` (`ndarray.tolist()`), so `speakers/domain` imports no numpy and stays importable by the service. Zero noise floors are clamped to `ENERGY_FLOOR` (1e-12) so digital silence never yields `Me` and no `log10(0)` exists (the dominance test is done as a linear ratio). Limits worth knowing: the noise floor is the 10th percentile, so a channel that is active in more than 90 % of the frames has no quiet reference; the bleed `k` needs 30 s of clearly active system audio or it is 0; channels of different length use the shorter one. "Unknown" needs the system turns (a stretch under a system turn belongs to that speaker). `merge_turns` makes the final list exclusive ("Me" wins, system and unknown turns are cut around it). The in-person decision (`choose_diarization_channel`, OQ-4) is "system audio is clearly active in under 1 % of the frames" using the same relative threshold. Sample selection takes a speaker's *clean* turns (`clean_turns_of` for diarized speakers, `me_turns_with_quiet_system` for "Me"), longest first; a remainder of the target shorter than the minimum clip ends the selection instead of adding a sliver.
- **Review fixes (F3.3b)**: "clearly active over the noise floor" has one source, `SPEAKERS_ME_NOISE_MARGIN_DB`, passed as `margin_db` to `estimate_bleed`, `choose_diarization_channel` and `me_turns_with_quiet_system` (the first draft hard-coded a second 10 dB constant). A frame the system envelope does not have is never "quiet": a Me turn reaching past a shorter or empty envelope is not a clean sample source. `SampleParams` and `SpeakersSettings` reject contradictory sample bounds (target below the minimum clip, minimum above the maximum) and `SPEAKERS_NUM_SPEAKERS` outside `MIN`/`MAX`. `MERGE_GAP_SECONDS` (0.3 s) is deliberately not a setting. `Turn.overlap` is the one place that decides "touching is not overlapping"; `assign_speakers` bisects the sorted turns (near-linear) instead of scanning all of them per word.

### GO-042 — pyannote.audio 4.0.7 facts the diarize worker depends on (read from the installed source)
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: (1) **Telemetry is ON by default**: `pyannote/audio/telemetry/config.yaml` ships `metrics_enabled: true` (OTLP to `otel.pyannote.ai`, recording pipeline name, audio duration and the speaker bounds). The switch is the environment variable `PYANNOTE_METRICS_ENABLED` (`false`/`0`), read on every call and only defaulted when absent. The service sets it (and `HF_HUB_DISABLE_TELEMETRY=1`) in every worker's environment, and `RealPyannoteBackend.prepare()` forces both again before the first import, so a user environment cannot turn it on. (2) **Access errors**: `Pipeline.from_pretrained(..., token=True)` goes through `huggingface_hub.hf_hub_download`; a gated model without accepted terms raises `GatedRepoError` (subclass of `RepositoryNotFoundError`, itself an `HfHubHTTPError`), a private or unknown one `RepositoryNotFoundError`, and `token=True` without a stored login `LocalTokenNotFoundError`. `pyannote` additionally `print`s a how-to message to stdout before re-raising `HfHubHTTPError`, which lands in `processing.log` thanks to the worker's fd 1 redirect (spec 6.2). The adapter maps those class names, or an HTTP status 401/403 on the exception's response, to `DIARIZATION_MODEL_ACCESS_DENIED`; the match is by class name over the MRO so the service never imports `huggingface_hub`. Offline or missing-cache errors (`LocalEntryNotFoundError`, `OfflineModeIsEnabled`, `HFValidationError`) become `MODEL_UNAVAILABLE`; `torch.cuda.OutOfMemoryError` (or "out of memory" in the text) becomes `GPU_OUT_OF_MEMORY`. (3) **Progress without terminal output**: `pipeline(file, hook=callable)` calls `hook(step_name, artifact, file=..., total=..., completed=...)`; `segmentation` and `embeddings` repeat with `total`/`completed`, `speaker_counting` and `discrete_diarization` fire once. A plain function is enough (`ProgressHook` is a rich terminal bar); the adapter maps segmentation to 0-40 %, embeddings to 40-95 % and the final step to 100 %. (4) The output is `DiarizeOutput(speaker_diarization, exclusive_speaker_diarization, speaker_embeddings)`; `Annotation.itertracks(yield_label=True)` yields `(Segment, track, label)`. (5) The in-memory input is `{"waveform": (channel, time) float32 tensor, "sample_rate": 16000}`; `torch.from_numpy(audio).unsqueeze(0)` shares the numpy memory. (6) `uv add --optional ml pyannote.audio` resolved pyannote.audio 4.0.7 with torch 2.14.1 and torchaudio 2.11.0 from the default PyPI index, pulling the cu13 NVIDIA wheels (`nvidia-cudnn-cu13` ...); the existing cu12 wheels for CTranslate2 stay: the two never share a process.

### GO-043 — The diarize stage chooses the channel before the cache key, and the cache holds only model output
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: the diarized channel (`system`, or `mic` for an in-person meeting, OQ-4) is part of the `turns.json` key, so the stage computes both 100 ms energy envelopes first (`WavChannelReader.read_energies`, one channel decoded and reduced at a time, so about 230 MB of float32 per hour plus a 1.6 MB float64 block; the energies are computed blockwise, never from a whole-signal float64 copy) and applies `choose_diarization_channel` with `SPEAKERS_ME_NOISE_MARGIN_DB`; only on a miss does it decode the chosen channel again and load pyannote. The cache holds only what the model produced (`diarization` and `exclusive_diarization` turns); "Me", assignment, grouping and samples are recomputed on every run and never cached (spec 4), so changing their thresholds needs no model run. The key is `{params: {pipeline, num/min/max_speakers, device, channel}, engines: {pyannote.audio, torch}, audio: fingerprint}`; the noise margin enters the key only through the channel it selects. Engine versions come from package metadata, so a cache hit imports no ML library. A test fixture needs quiet frames: the activity threshold is relative to the channel's own noise floor, so a constant tone looks like "no speech" (see GO-041).

### GO-044 — The extraction step is cheap CPU work that re-runs every time, and it needs words, turns and both envelopes
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: after pyannote (`DIARIZING`) the same worker process runs `AssembleTranscript` without a model resident: "Me" detection over the two envelopes (skipped when the mic was diarized, in person), `merge_turns` (Me wins its overlaps), `assign_speakers`, `group_utterances`, `number_speakers`, then it emits the `stage` event `EXTRACTING_SAMPLES` and writes the samples, `utterances.json` and `transcript.md`. `words.json` is read first, before any GPU work, and any file under its own key is accepted (`LoadWords`): a missing file fails the job with `TRANSCRIPTION_FAILED` instead of yielding an empty meeting. Samples: remote speakers (and every speaker in an in-person meeting) are cut from the diarized channel using the regular, overlapping annotation's clean turns; "Me" and "Unknown" from the microphone using only turns during which the system channel is quiet (`me_turns_with_quiet_system`). Each source channel is decoded at most once, only when somebody needs a sample from it. A run is staged under `.assembling/` and committed together after every file is written (the previous files move into `.commit.old/` behind a manifest, the staged ones move in, and renaming it to `.commit.done` is the commit point; `samples/` is swapped in whole, so a speaker that disappeared between runs leaves no stale file); any failure rolls back at once and a crash is rolled back by the next `begin`/`commit`, so the meeting folder always holds the previous complete set or the new complete one, never a mix (each staged file is also written as `.<name>.tmp` then renamed). A file that was not staged keeps its previous copy; a sample is peak-normalized to -3 dBFS and clips are 0.4 s apart. `turns.json` gets `bleed`, `me_turns` and `unknown_turns` added to its `data` after every run (the key does not change, so the cache still hits). `utterances.json` lists `{speaker, start, end, text}` with the stable speaker id (`ME`, `S1`...), never the pyannote label. A speaker is listed only when it owns an utterance; `talk_seconds` is the sum of its utterance durations.

### GO-045 — Processing is wired through a recording port, one loop thread and a bounded shutdown
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `recording` defines `RecordingListener` (`on_started`, `on_finished`; a null object by default) and knows nothing about who listens; `meeting_scribe/handoff.py` (composition level, the only module that sees both domains) adapts it to `ProcessingController`. `on_started` fires once capture is running (a failed `Start` is never announced) and calls `preempt()`; `on_finished` fires once per started session, however it ends (clean stop, crash found by the poll or by `Stop`, a stop that failed), enqueues it (a `FAILED` capture too, OQ-1) and then `release()`s the GPU (in a `finally`: a raising enqueue still releases it). A listener exception is logged and never changes the recording. A session that cannot be enqueued is logged at ERROR with its code; there is no automatic folder scan (OQ-2), so it is not resumed. `serve()` calls `resume()` after it owns the bus name (a second instance resumes nothing), then runs `serve_forever` in a daemon thread; at shutdown it stops the recording first (which queues its meeting for the next start), then `shutdown()` and a bounded `join` in a worker thread so the event loop is never blocked (`SHUTDOWN_TIMEOUT_SECONDS`, 20 s: 5 s grace plus kill and the post-result wait). A loop that outlives the bound is logged at ERROR; the job stays resumable on disk. The loop thread starts inside the `try` that guarantees the shutdown. An unexpected exception in a job is recorded as `FAILED` (`INTERNAL_ERROR`, fixed message), announced, and the return to idle is published. `build_processing_config` copies every setting into the job (`ProcessingSettings` plus `TranscriptionSettings.params()` and the whole `SpeakersSettings` dump, which `DiarizationParams` and `AssemblyParams` each narrow); a test compares every field, so a new setting that is not forwarded fails it.
- **Source**: [processing spec](../specs/processing.md) section 1 and 6.3 (OQ-1, OQ-3)
- **Verifiable in**: `tests/unit/test_handoff.py`, `tests/unit/test_main.py`, `tests/e2e/processing/`

### GO-046 — Meetings1 reads the controller, announces from the processing thread through the loop, and the notifier is isolated
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `MeetingsInterface` talks only to `ProcessingController` (`list_meetings`, `get_meeting`, `reprocess`, `status`); blocking repository calls run in `asyncio.to_thread`. It is also a `ProcessingEvents` observer, subscribed on the `ProcessingEventHub` that `create_app` builds (the interface only exists once the bus is connected): events arrive on the processing thread and are moved to the event loop with `call_soon_threadsafe`, so signals and `PropertiesChanged` are emitted from the loop thread and keep the order in which the controller persisted them. `status_changed()` carries no data on purpose: the loop-side handler reads the freshest `status()` and publishes only a real change, so out-of-order callbacks cannot publish a stale status. The controller tracks the active job under its lock (`ActiveState` is `IDLE` after a preemption, although the job's state on disk stays in progress). Signals for states reached before a client subscribes are not replayed; clients read the properties and `Get` first. The notifier is a port (`Notifier`, `NotifyOnFinish` observer, `FreedesktopNotifier` adapter): the adapter runs its own `asyncio.run` with a fresh session-bus connection per notification (the processing thread has no loop and the service bus is never shared across threads), uses a raw `Notify` call (no introspection of the daemon) and returns `Failure(NOTIFICATION_FAILED)` for a missing daemon, a lost bus or a timeout, which is logged at WARNING and never touches the job. The text is `Meeting processed: N speakers` (singular for one, `no speech detected` for none, `(CPU)` when a stage ran on the CPU) or `Meeting processing failed` with the first line of the failure message (at most 200 characters); never transcript text or speaker names. Tests use `FakeNotifier`; the adapter itself is tested against a fake `org.freedesktop.Notifications` on the private bus.
- **Source**: [processing spec](../specs/processing.md) section 8, AC-18, AC-19
- **Verifiable in**: `tests/e2e/meetings/`, `tests/unit/meetings/test_notifications.py`

---

## Statistics (keep updated)

| Category | Active | Superseded | Deprecated |
|---|---|---|---|
| BR | 4 | 0 | 0 |
| CR | 13 | 0 | 0 |
| GO | 66 | 1 | 0 |
| AP | 4 | 0 | 0 |

**Last rule added**: GO-067 — 2026-10-12
**Next available IDs**: BR-006, CR-014, GO-068, AP-005

---

## How to deprecate or supersede a rule

Mark the old rule `Status: superseded` with `Superseded by: <ID> (date)` and the reason; create the new rule
with `Supersedes: <ID>`. Never delete rules.

### GO-047 — Models are fetched at install time; the worker is always offline
- **Date**: 2026-10-09
- **Status**: superseded
- **Superseded by**: GO-066 (2026-10-12): the installer no longer downloads the models (F6.3, [install spec](../specs/install.md)); the download moves to the service and Preferences. The worker stays offline.
- **Detail**: `make install` ran `meeting-scribe prefetch-models` after the service was switched and started (recording never needs the models, so a missing network or Hugging Face login must not block installing it; a failure is only reported in the summary, never as an install failure). The command is `meeting_scribe.cli:run` (the console script), not `main`, because the import contract forbids `main` from reaching the ML backends. It fetches the gated pipeline first (a missing login fails in seconds, not after the 1.6 GB Whisper model), then Whisper, always attempts both, and maps failures with the same mappers as the worker (`DIARIZATION_MODEL_ACCESS_DENIED` with "accept the terms at <url> and run `hf auth login`"); a `MODEL_UNAVAILABLE` mapping is re-worded for install time (what failed, check network / model name setting / cache, retry `meeting-scribe prefetch-models`) because the mapper's own text describes the offline worker; anything shaped like a token (`hf_...`) is scrubbed from the output and the token is never read (`token=True` is the ambient login). The real fetchers are methods of the two backends: `FasterWhisperBackend.download_model` (`faster_whisper.utils.download_model`, which also fills the cache `WhisperModel` reads) and `RealPyannoteBackend.fetch_pipeline` (the worker's own `Pipeline.from_pretrained`, CPU only, so a successful fetch guarantees an offline load). The summary does not hard-code a pipeline URL: the prefetch output above names it. `SubprocessWorkerRunner` forces `HF_HUB_OFFLINE=1` in every worker environment (a user value cannot turn it off); a cache miss then surfaces as `LocalEntryNotFoundError` and becomes `MODEL_UNAVAILABLE` with "run `make install` again". `uv tool install` takes the extra through a PEP 508 direct reference (`meeting-scribe[ml] @ <repo>`); `<repo>[ml]` is a parse error in uv 0.10.9.
- **Source**: [processing spec](../specs/processing.md) OQ-7, section 9, F3.5
- **Verifiable in**: `tests/unit/test_prefetch.py`, `tests/unit/test_cli.py`, `tests/unit/test_install_scripts.py`, `tests/unit/meetings/test_subprocess_worker_runner.py`

### GO-048 — A recording that cannot be queued tells the user; the extension never shows a stale result
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: when `enqueue` fails after Stop (storage error), `ProcessingRecordingListener` logs the code and sends one desktop notification with fixed text (no meeting id, no content): the recording is saved, its processing could not be queued, check the service log and the runbook (no retry is promised: a restart only resumes a `meeting.json` that exists). A `meeting.json` that did get written is resumed at the next start by `resume()`; a recording folder without one is not rediscovered. The GPU is released in every case (GO-045). In the extension, `MeetingsClient` forgets the last result when a job starts (`ActiveState` leaves `IDLE`) and whenever the service appears or vanishes, so a missed `ProcessingFinished` cannot resurrect a handled failure; a proxy that cannot be built is logged once through `logError` and the client stays unavailable.
- **Source**: [processing spec](../specs/processing.md) section 8
- **Verifiable in**: `tests/unit/test_handoff.py`, `gnome-extension/tests/meetingsClient.test.js`

### GO-049 — A speaker review edit is two saves of meeting.json around the transcript write, under the processing lock
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: `ReviewController` saves `meeting.json` with `speaker_review.transcript_stale = true`, rewrites `transcript.md`, then saves it again with the flag cleared; the edit is never lost, a crash in between leaves the flag set and `repair_stale()` (service start) or the next call redoes the last two steps. Every call runs inside `MeetingLock.hold()` (the processing controller's condition, re-entrant) and only on a `DONE` meeting (`MEETING_NOT_PROCESSED` otherwise). `Meeting.reprocess()` and `complete()` reset `speaker_review` and the new run replaces the names (OQ-1). The speakers package's `TRANSCRIPT_CORRUPT` and `TRANSCRIPT_STORAGE_FAILED` become `MEETING_CORRUPT` and `MEETING_STORAGE_FAILED` at this boundary. Errors never quote a name. The D-Bus adapter (F4.3) serves it: `GetSpeakers`, `GetTranscript`, `GetSpeakerSample` (bytes read through the `SampleReader` port, bounded to 1 MiB, resolved inside the meeting folder, listed id only) and the three edits run in `asyncio.to_thread`; `SpeakersChanged` is emitted only when an edit changed something and when a job ends `DONE`. `create_app` wires the controller with `ProcessingController` as its lock and `serve()` calls `repair_stale()` right after `resume()`.
- **Source**: [review app spec](../specs/review-app.md) section 2, AC-10, AC-11, F4.2
- **Verifiable in**: `tests/unit/meetings/test_review_controller.py`, `tests/unit/meetings/test_meeting.py`

### GO-050 — The review app's `ui` package is not type checked; its logic lives in `core`
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: `gi` has no stubs and is not installed in the project venv (Python 3.12), so pyrefly cannot resolve `gi.repository`. Decision (the fork deferred from F4.4): `pyrefly.toml` excludes `review-app/src/meeting_scribe_review/ui/**` through `project-excludes`; `core` stays fully checked, and ruff and import-linter still cover `ui`. A local stub for Gtk, Adw and Gio was rejected: it would be hand-written API knowledge that drifts from the installed typelibs, for modules that only bind presenters to widgets. The compensating controls are the import smoke test on system Python (`tests/unit/review_app/test_ui_imports_on_system_python.py`, which also asserts that importing opens no display), the variant codec test run under `/usr/bin/python3`, and `tests/e2e/meetings/test_review_app_gateway.py`, which drives `DBusGateway` against the real `MeetingsInterface` on a private bus. Keep `ui` modules declarative: anything with a branch worth testing moves to `core` (`ReviewSession`, `AvailabilityFilter`, `dispatch_signal`, `meeting_id_from_options`). `ruff.toml` ignores N802 (`do_*` virtual methods) and PLW0108 (late-bound lambdas) for `ui` only.
- **Source**: [review app spec](../specs/review-app.md) section 6, F4.4, F4.5a
- **Verifiable in**: `pyrefly.toml`, `tests/unit/review_app/test_ui_imports_on_system_python.py`, `tests/e2e/meetings/test_review_app_gateway.py`

### GO-051 — PyGObject and libadwaita facts the review app depends on (verified on PyGObject 3.56.3, GTK 4.22, libadwaita 1.9)
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: (1) `GLib.Variant.unpack()` turns `ay` into a list of ints; `unpack_reply` reads it through `get_data_as_bytes()` so a sample arrives as `bytes`. `a{sv}` rows unpack to plain dicts and `a(...)` to lists of tuples, which is what the parsers take. (2) `from gi.repository import Gtk` initialises GTK on import; with no display `Gdk.Display.get_default()` is `None` and `Gtk.is_initialized()` is still `True`, so the import test runs with no display variable and asserts on the display. (3) `Adw.StatusPage` is final and cannot be subclassed (`could not create new GType`); wrap it. (4) `Gio.DBusProxy.new_for_bus` with `DO_NOT_AUTO_START_AT_CONSTRUCTION` never wakes the service by merely watching it; a method call still activates it, and an absent service answers `org.freedesktop.DBus.Error.ServiceUnknown`, read with `Gio.DBusError.get_remote_error` (never the message, which can hold private text). (5) `Gio.bus_watch_name` reports the current state of the name once at start; `AvailabilityFilter` treats that first report as a baseline so start-up does not flash a "not running" error. (6) `Gio.Application` with `HANDLES_COMMAND_LINE` takes `--meeting ID` through `add_main_option` and `do_command_line`; `command_line.get_options_dict().end().unpack()` is a plain dict. (7) `Adw.NavigationSplitView`, `Adw.Breakpoint`, `Adw.Banner`, `Adw.ToolbarView`, `Adw.NavigationPage.new(child, title)` exist with the names used in `ui/window.py` (checked on the installed typelib and in the libadwaita docs through Context7).
- **Source**: [review app spec](../specs/review-app.md) section 6, "Library facts to verify"
- **Verifiable in**: `tests/unit/review_app/test_dbus_codec.py`, `tests/unit/review_app/test_ui_imports_on_system_python.py`, `tests/e2e/meetings/test_review_app_gateway.py`

### GO-052 — The review app installs next to the service version, on system Python, with a desktop entry
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: (1) `install.sh` copies the app and renders the launcher inside `versions/<id>/` before `current` moves, so a rollback restores service and app together; the launcher pins the interpreter (`REVIEW_APP_PYTHON`, default `/usr/bin/python3`) because a venv or `uv` Python has no PyGObject. `REVIEW_APP_PYTHON` is an interpreter override: tests substitute a fake, and `make run-review-app` uses it too. (2) The desktop entry is rendered by bash splitting on the first `@EXEC@`, so the template must contain that token exactly once (a mention in a comment would be rendered instead of the `Exec` line); its `Exec` uses the stable `current/` path, so it needs no rewrite on upgrade, and it is written only after the switch (a rollback removes it when the restored version has no `bin/meeting-scribe-review`, so a pre-F4 version never leaves a dangling entry); the single `Exec` argument is always double-quoted and escaped per the Desktop Entry specification (`"`, backtick, `$` and `\` escaped, `%` doubled, backslashes doubled again at file level), so data directories with spaces or reserved characters work. (3) The toolkit check is three read-only imports and only warns; the summary line is `review app: ready` or `review app: NOT usable (missing: ...)`. (4) `uninstall.sh` removes only the desktop entry itself; the launcher and the app go with `versions/`. (5) Tests substitute `python3` and `update-desktop-database` fakes; no test starts the app.
- **Source**: [review app spec](../specs/review-app.md) section 8, F4.6
- **Verifiable in**: `scripts/install.sh`, `scripts/uninstall.sh`, `packaging/applications/dev.meetingscribe.Review.desktop.in`, `tests/unit/test_install_scripts.py`

### GO-053 — The "Review last meeting" item launches a desktop entry and never touches meeting data
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: (1) The item is offered whenever the service is available and the state is not `RECORDING` (`IDLE`, `STOPPED` or `FAILED`; never in an unknown state, which stays inert), because the extension cannot know whether a `DONE` meeting exists without calling `List`, which the meetings client contract forbids; the app's empty state covers it. It is hidden while a call is in flight, in every state (the menu then shows only "Working…" or the inert unknown-state line). (2) `launchReview()` is injected into the controller and implemented in `extension.js` as `Gio.DesktopAppInfo.new('dev.meetingscribe.Review.desktop')?.launch([], null) ?? false`: a missing entry (`new` returns `null`) and a failed launch both count as failure, and the controller shows one fixed notification (`launchFailureMessage`) and never the launch error, which can hold paths. (3) The launch is a short, non-blocking hand-off to the shell's app launcher, with no ML and no D-Bus call of ours, so it stays within ADR-001; it needs a check in a real shell. (4) A `desktop-entry` notification action stays out of F4 (OQ-6).
- **Source**: [review app spec](../specs/review-app.md) section 8, AC-16, A-7, A-8; F4.7
- **Verifiable in**: `gnome-extension/tests/indicatorModel.test.js`, `gnome-extension/tests/indicatorController.test.js`, `gnome-extension/tests/shellGlue.test.js`

### GO-054 — One GPU lease serves recording, processing and local summaries
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: `GpuLease` (port in `meetings/domain/ports.py`, `InMemoryGpuLease` in `meetings/application/gpu_lease.py`) replaces a second scheduler. A recording is always granted and revokes every other holder (their `on_revoke` runs outside the lease lock); a `processing` worker or a local `summary` is granted only when nobody holds it, and it is not re-entrant. `ProcessingController.preempt()` and `release()` take and give the `recording` holder; `run_next()` takes `processing` for the whole job and `_can_start()` reads `available("processing")`, so the loop never spins while a summary holds the GPU. When the summary ends it releases the lease and calls `on_gpu_free` (wired to `ProcessingController.wake()`), because the loop sleeps on its condition. The cloud summary takes no lease. The summary's model is released (`GenerateSummary` finally block) before the lease is given back, so Whisper never loads next to a resident LLM.
- **Source**: [summaries spec](../specs/summaries.md) section 7, AC-12; F5.4
- **Verifiable in**: `tests/unit/meetings/test_gpu_lease.py`, `tests/unit/meetings/test_processing_queue.py`, `tests/unit/meetings/test_summary_controller.py::test_local_summary_respects_the_gpu`

### GO-055 — A summary save is the file, then meeting.json, under the meeting lock; a failure never touches the old file
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: `SummaryController` holds the shared `MeetingLock` only to snapshot and to save, never during the model call. The save re-checks `DONE`, writes `summary.md` (temp file, a copy of the current one as `summary.previous.md`, then an atomic replace, so a failed write leaves `summary.md` as it was and exactly one backup exists), then saves the `summary` section; a crash in between leaves a file without a section, which reads as "no summary". A failed attempt records `last_failure` (code and time, never content) on the section without touching the file; a cancel records nothing; a meeting that is no longer `DONE` discards the result with `MEETING_NOT_PROCESSED` and records nothing. `SummaryFinished` is emitted after the slot and the lease are free, so a client that refreshes on it sees `DONE`, not `RUNNING`. `Meeting.complete` keeps `files["summary"]` when a summary exists; `Meeting.reprocess` never touches the section.
- **Source**: [summaries spec](../specs/summaries.md) sections 7 and AC-1, AC-10, AC-13, AC-15; F5.4
- **Verifiable in**: `tests/unit/meetings/test_summary_controller.py`, `tests/unit/meetings/test_filesystem_summary_files.py`, `tests/unit/meetings/test_meeting.py`

### GO-056 — The cloud key is read only for a confirmed cloud request that nothing else refused
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: The `Summarize` gates run in this order: unknown provider (`INVALID_VALUE`), cloud not selected (`SUMMARY_CLOUD_DISABLED`: the provider setting is not `cloud`), not confirmed, then the `DONE` and no-speech snapshot, then the single slot and the GPU, and only then `CloudKeyStore.get_key()` (a missing, locked or unreachable key refuses the call and frees the slot). `Providers` never reads the key, so its cloud row says only whether the cloud is selected; a missing key shows when a confirmed request runs. The pre-check key is dropped at once; the provider reads it again for its own request (BR-005).
- **Source**: [summaries spec](../specs/summaries.md) AC-5, AC-6, section 8a; BR-005; F5.4
- **Verifiable in**: `tests/unit/meetings/test_summary_controller.py::test_cloud_gates_follow_the_provider`, `::test_providers_rows`, `::test_no_secret_in_logs`

### GO-057 — Summaries1 is a thin adapter; one lease and one hub are wired in the composition root
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: `SummariesInterface` (`meetings/infrastructure/dbus/`) is also the controller's observer, subscribed to a `SummaryEventHub` (the controller is built before the bus exists). Events arrive on the job thread and cross to the loop with `call_soon_threadsafe`; `SummaryProgress` is throttled to one per second by `ProgressThrottle`, which always lets the last step through and restarts at every start and end. `ActiveSummaryMeetingId` is announced only when it changed. `create_app` builds ONE `InMemoryGpuLease`, passes it to `ProcessingController` (recording goes through `preempt()`/`release()`) and to `SummaryController`, and passes `ProcessingController.wake` as `on_gpu_free`; the providers, labels (the "cloud" label), limits and the heading catalog come from `SummariesSettings` and a `SummaryAdapters` bundle that tests replace with fakes. `serve` cancels and joins a running summary before it stops the processing loop; `SummaryController.shutdown(timeout)` bounds the join itself (no executor thread stays blocked), the job thread is a daemon, and a job that ignores the cancel past `SHUTDOWN_TIMEOUT_SECONDS` is logged (meeting id only) and makes the exit code 4, the same as a stuck processing loop (GO-026; no worker process is left over, but the unit is restarted all the same). A `dbus_method` wrapper discards the return value when called from Python (GO-022), so the behavior lives in plain coroutines the unit tests call. `LastSummary` skips a meeting whose `summary.md` was deleted and returns the path from the file store, never one built by a client.
- **Source**: [summaries spec](../specs/summaries.md) section 10, AC-19; GO-022, GO-054; F5.5
- **Verifiable in**: `tests/unit/meetings/test_summaries_interface.py`, `tests/unit/meetings/test_summary_events.py`, `tests/unit/test_main.py::test_recording_processing_and_summaries_share_one_gpu_lease`, `tests/e2e/meetings/test_summaries_over_dbus.py`

### GO-058 — "Open last summary" opens the service's path through Gio and never builds one
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: (1) `MeetingsClient` also watches `Summaries1`: it calls `LastSummary` when the service appears, on `SummaryFinished` and when the menu opens, and keeps `{startedAt, path}` in its snapshot; `ENTITY_NOT_FOUND_SUMMARY`, an older service without the interface, a timeout or a path that is not absolute all clear it, which hides the item. A request counter drops a late answer. (2) The item is `Open last summary (<weekday, day, month, 24-hour time>)`, built from `started_at` in the user's locale (`formatStartedAt`); it carries no title and no path, and it follows the "Review last meeting" visibility (hidden while recording, busy, unknown state or unavailable service). (3) `openSummary(path)` is injected into the controller and implemented in `extension.js` as `Gio.AppInfo.launch_default_for_uri_async(Gio.File.new_for_path(path).get_uri(), null, null)`, so the Shell never blocks and the default app for `.md` handles it; a `false`, a throw or a rejection shows one fixed notification (`summaryOpenFailureMessage`), never the error, which can hold the path. (4) The extension never reads a summary and never writes the file name `summary.md`; a static test checks it. It needs a check in a real shell.
- **Source**: [summaries spec](../specs/summaries.md) section 10, AC-19, OQ-7, A-8, A-9; F5.7
- **Verifiable in**: `gnome-extension/tests/indicatorModel.test.js`, `gnome-extension/tests/indicatorController.test.js`, `gnome-extension/tests/meetingsClient.test.js`, `gnome-extension/tests/shellGlue.test.js`

### GO-059 — The saved settings are a source below `.env`, entered through a context variable
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: pydantic-settings puts `init` above the environment, so passing the user's saved values as constructor arguments would let them beat `.env`. `file_layer(values)` (`base/infrastructure/settings.py`) instead sets a `ContextVar`; `MeetingScribeSettings.settings_customise_sources` adds an `InitSettingsSource` built from the values of its own section (the env prefix, lowercased) between the dotenv and the file-secret sources. Only `Settings.load(file_values=...)` and `Settings.with_overrides(values)` enter the layer, so every other constructor keeps `init > env > .env > default`. The source per exposed field is computed once at load from `os.environ`, the `.env` keys and the saved keys (`Settings.source_of`). `with_overrides` builds each exposed section alone first so a `ValidationError` maps to its field key (an env-only field or a cross-field rule maps to the section name), with the validators' own messages and never the value. The `preferences` file store moves a file that was read and is invalid (JSON, version or schema) aside on the next successful write (`settings.json.invalid`, then `.invalid.1`, `.invalid.2`; a backup is never overwritten), while a file it cannot read at all (permissions, I/O error, a directory) is kept and the write fails with `SETTINGS_STORAGE_FAILED` (errno logged) and logs keys only (BR-005, spec AC-11, AC-14).
- **Source**: [preferences spec](../specs/preferences.md) sections 3 and 6, AC-3, AC-10, AC-11; F5.10 P1
- **Verifiable in**: `tests/unit/test_settings_overrides.py`, `tests/unit/preferences/test_settings_file.py`

### GO-060 — The summary config is read per request and the legacy cloud switch can only keep the cloud off
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: `SummaryController` takes a `SummaryConfigSource` (a callable) and reads it once per `Summarize`; the gate, the snapshot and the job use that one reading, and the job keeps its own `total_timeout_seconds`, so a save during a running summary never changes it (AC-6). `LiveSummaryConfig` in `main.py` is the source: `apply(settings)` rebuilds the providers, labels and limits (the real adapters are built again from the settings; nothing is sent and no key is read) and swaps one reference. `SummaryConfig.cloud_enabled` is derived from `default_provider == "cloud"` (the tests that need both providers usable build the rig with provider `cloud`; the `default` flag of the rows follows the provider, so the local row is no longer the default there). The obsolete `SUMMARIES_CLOUD_ENABLED` is read once at load from the environment or `.env`; the cloud is opt-in, so a provider `cloud` from the environment or `.env` without an explicit `true` (absent, false or unparseable), and an explicit `false` over a saved cloud, resolve the provider to `local`, locked with `lock_reason` `LEGACY_SETTING` and one WARNING naming the variable (never a value); a provider saved in the window is the opt-in by itself; `true` with provider `local` is ignored with one WARNING saying the per-request cloud is gone (one button); `with_overrides` never repeats the warning. Remove the rule one release after the Preferences window ships.
- **Source**: [preferences spec](../specs/preferences.md) sections 5 and 7, AC-4, AC-6, AC-13; F5.10 P2
- **Verifiable in**: `tests/unit/meetings/test_summary_controller.py::test_config_swap_keeps_the_running_job`, `tests/unit/test_settings_overrides.py::test_legacy_false_overrides_a_saved_cloud_too`, `tests/unit/test_main.py::test_applying_new_settings_swaps_the_summary_config_without_a_restart`

### GO-061 — Settings1 saves on a worker thread, replies before it restarts, and probes systemd with LoadUnit
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: (1) `PreferencesService.set` is one transaction under a lock: per-field checks (unknown, locked by the environment, the meetings-folder check), then `Settings.with_overrides` on the merged saved values, then the atomic file write, then the live apply and the signals. Any problem returns rows and leaves the file, the effective settings and the signals untouched; only a failed write is a D-Bus error. A value equal to its default is dropped from the file. The meetings-folder check has no side effect; the folder is created (mode 0700) by a separate `prepare` step only after every field passed. If the live apply raises after the save, the values stay saved, the signals are sent, and the reply carries `apply_failed` (the type name is logged, never a value); `GetAll` keeps saying so in every row's `apply_problem` until a later apply succeeds. If the file write itself fails, a folder that `prepare` just created is removed again while it is still empty. An empty `Set` writes nothing. (2) The interface runs the service on `asyncio.to_thread` and crosses back to the loop with `call_soon_threadsafe` for `SettingsChanged` and the `RestartRequired` `PropertiesChanged`, like `Summaries1`. `restart_required` is every restart field whose saved value differs from the one the controllers captured at start. (3) `Restart` is two steps because a reply cannot follow a restart: `check` refuses when busy (`SETTINGS_RESTART_BUSY`) or when the manager does not answer `Manager.LoadUnit("meeting-scribe.service")` (`SETTINGS_RESTART_UNAVAILABLE`, with the manual command), the interface replies `RESTARTING`, and `perform` runs 0.3 s later, looks at the activity once more and calls `Manager.RestartUnit(name, "replace")` over a short-lived session-bus connection (verified in `man org.freedesktop.systemd1`, systemd 259). A restart that fails after the reply is only logged. (4) Tests answer as a fake `org.freedesktop.systemd1` on the private bus; nothing calls the real user manager. (5) The e2e rig loads its settings from a tmp `settings.json` and an isolated XDG folder (an empty `.env` there beats a development `./.env`), because `with_overrides` rebuilds from the environment and saved values, never from hand-built settings objects.
- **Source**: [preferences spec](../specs/preferences.md) sections 4 to 6, AC-2, AC-8, AC-14; F5.10 P3
- **Verifiable in**: `tests/unit/preferences/test_update_settings.py`, `tests/unit/preferences/test_restart.py`, `tests/e2e/preferences/test_settings_over_dbus.py`

### GO-062 — Cloud models are listed from the provider and gated by a documented allowlist of families
- **Date**: 2026-10-11
- **Status**: active
- **Detail**: (1) `GET {cloud_base_url}/models` (OpenAI list format, verified against OpenCode Go on 2026-10-11: 38 entries with only `id`, `object`, `created`, `owned_by`) does not say which endpoint serves a model, so a model is supported when its id starts with a family served on `/chat/completions` per <https://opencode.ai/docs/go>: `deepseek-`, `glm-`, `kimi-`, `mimo-`, `longcat-` (`CLOUD_CHAT_COMPLETIONS_FAMILIES` in `summaries/infrastructure/model_listing.py`, the only place). Every other model is listed as `NOT_SUPPORTED_YET`; adding a family is one line plus the spec note. (2) The service reads the key from the keyring for each listing and sends it only as a bearer token to the configured https base; it is never logged, returned or put in an error (BR-005). The local list (`GET <origin>/api/tags`) uses a client with `trust_env=False`, like the releaser. (3) An unavailable list is a `ListModels` status. `Set` validates a model change against the last good list of its scope (cached 10 minutes in `LiveSummaryConfig`) and never fetches one, because it holds the settings lock; without a fresh list it accepts the save. Each listing has a total 5 s (local) or 10 s (cloud) deadline enforced by a helper thread, not by per-phase `httpx2` timeouts. (4) `Adw.ComboRow` has no per-item sensitivity, so unsupported models are a greyed list under the dropdown.
- **Source**: [preferences spec](../specs/preferences.md) AC-16 to AC-20, D-1 to D-4; F5.10b
- **Verifiable in**: `tests/unit/summaries/test_model_listing.py`, `tests/unit/preferences/test_model_check.py`, `tests/e2e/preferences/test_settings_over_dbus.py`

### GO-063 — OpenCode Go needs a client name and a per-job session id on every request
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: (1) The gateway answers HTTP 400 `MissingSessionID` ("Request is missing x-opencode-session") to requests without session identification; with `User-Agent: meeting-scribe/<package version>` and `x-opencode-session: <opaque id>` it answers 200 (validated by the owner on 2026-10-11; source: <https://opencode.ai/docs/go>, "Where can I use it"). (2) The requirement is part of the provider entry: `CloudProvider.session_header` in `CLOUD_PROVIDERS` (`summaries/infrastructure/summaries_settings.py`). `OpenAiCompatibleProvider` sends the `User-Agent` on every cloud request and the session header on every chat request; `OpenAiCompatibleModelLister` sends the `User-Agent` on the `/models` listing. (3) The session id is `new_session_id()` (`summaries/domain/session.py`): the first 32 hex characters of the SHA-256 of a random UUID, created once per summary job and shared by all its steps through `SummaryProvider.complete(session_id=...)`. It is never derived from the meeting id, names or content and is never logged. (4) A local provider sends neither header. (5) Superseded 2026-10-12: `reasoning_effort` is no longer sent by anything. The local provider is the native Ollama adapter, which sends `think: false` instead, and `OpenAiCompatibleProvider` is cloud-only (see GO-064 and the summaries spec, section 5).
- **Source**: [summaries spec](../specs/summaries.md) section 5; [ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md); F5.9 validation
- **Verifiable in**: `tests/unit/summaries/test_openai_adapter.py`, `tests/unit/summaries/test_model_listing.py`, `tests/unit/summaries/test_generate_summary.py`, `tests/unit/summaries/test_client_identity.py`

### GO-064 — Local summaries use native Ollama with an explicit context; an overfull prompt loses its beginning silently
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: (1) Ollama's OpenAI-compatible endpoint cannot set `num_ctx` per request (it stays at 4096), so the local provider is `OllamaChatProvider`, which calls the native `POST <origin>/api/chat` with `options.num_ctx` = `SUMMARIES_LOCAL_CONTEXT_TOKENS`, `options.num_predict`, `think: false` and `stream: true`; the OpenAI-compatible adapter is only for the cloud. (2) When the prompt exceeds `num_ctx`, Ollama silently truncates the BEGINNING of the prompt (the system instructions) and the model ignores the format; the final chunk reports `prompt_eval_count` equal to `num_ctx`. The adapter fails with `SUMMARY_CONTEXT_TOO_LARGE` when `prompt_eval_count >= num_ctx - 16`, before it looks at `done_reason == "length"` (`SUMMARY_OUTPUT_LIMIT_REACHED`), because a full window also ends as `length`. (3) Whether a model fits the GPU is a measurement, not arithmetic: check placement with `ollama ps` (the `PROCESSOR` column must read `100% GPU`). On the owner's 8 GB RTX 5060, `qwen3.5:9b` loads at 8.8 GB (27% CPU) and `granite4.1:8b` at 16k took 35 minutes; `qwen3:4b-instruct-2507-q4_K_M` is 100% GPU at 16k (peak about 5 GB). (4) Output is bounded by the prompts as well as by the token caps (`SUMMARIES_NOTES_MAX_OUTPUT_TOKENS` 768 for map and merge, `SUMMARIES_MAX_OUTPUT_TOKENS` 3072 for the final summary): a token cap only cuts the answer. (5) The model is still released with `keep_alive: 0` after a local summary.
- **Source**: [summaries spec](../specs/summaries.md) sections 4 and 5; [ADR-006](../decisions/006-summary-provider-openai-compatible.md); Ollama docs (`/api/chat`, `modelfile`); measurements of 2026-10-12
- **Verifiable in**: `tests/unit/summaries/test_ollama_provider.py`, `tests/unit/summaries/test_generate_summary.py`, `tests/unit/summaries/test_prompt_builder.py`

### GO-065 — The token estimate is 2.25 characters per token, measured, and must undercount
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: The chunker has no tokenizer, so `CHARS_PER_TOKEN` (`summaries/domain/chunker.py`, the only place) must stay below the real ratio. Measured 2026-10-12 with the qwen3:4b-instruct tokenizer on a Spanish meeting dialogue (timestamps and speaker prefixes): 22,397 characters = 8,883 prompt tokens, 2.52 characters per token. The earlier value of 3 overcounted the characters per token (undercounted tokens) by about 16%: a 2-hour meeting of 45,782 characters went into one map request and Ollama truncated it. The value is 2.25, that ratio minus about 10%. Re-measure it when the default model changes; another language or tokenizer can be denser still, and the truncation guard (GO-064) is the backstop, not the plan.
- **Source**: [summaries spec](../specs/summaries.md) section 4, A-7
- **Verifiable in**: `tests/unit/summaries/test_chunker.py`, `tests/unit/summaries/test_generate_summary.py::test_a_two_hour_dialogue_sized_transcript_is_split_by_the_measured_ratio`

### GO-066 — The installer never downloads the models; it reports `models: ready (cached)` or `PENDING`
- **Date**: 2026-10-12
- **Status**: active
- **Supersedes**: GO-047 (the install-time download)
- **Detail**: `./install.sh` (alias `make install`) does no network step: recording needs no model, so a missing network, login or terms acceptance can never block or fail the install. After the switch it runs `current/bin/meeting-scribe prefetch-models --check` (offline) and prints `models: ready (cached)` or `models: PENDING: add your Hugging Face token in Preferences and press Download models; recording works meanwhile`, followed until F6.7 by `(until then: meeting-scribe prefetch-models)`. A missing, failing or timed out check is reported as PENDING, never as a failure. The check is bounded by `MEETING_SCRIBE_MODELS_CHECK_TIMEOUT` seconds (default 30; an invalid value falls back to 30). The `--check` option arrives in F6.4, so until then the real command rejects it and the line says PENDING. Unchanged from GO-047: the worker always runs with `HF_HUB_OFFLINE=1`, a cache miss becomes `MODEL_UNAVAILABLE`, and `uv tool install` takes the extra as `meeting-scribe[ml] @ <repo>`.
- **Source**: [install spec](../specs/install.md) section 5 and OQ-2
- **Verifiable in**: `tests/unit/test_install_scripts.py`

### GO-067 — Processing loads the cache with no token; `prefetch-models` is the one command that takes one, on stdin
- **Date**: 2026-10-12
- **Status**: active
- **Detail**: `RealPyannoteBackend.load_pipeline` calls `Pipeline.from_pretrained(pipeline, token=None)`: the worker is offline (`HF_HUB_OFFLINE=1`), so a cached model loads with no network, no login and no token, and the loader never reads a stored login (proved on the real cache by `tests/gpu/test_offline_cached_load.py`, which redirects `HF_TOKEN_PATH` to a missing file). Only the download needs a token: `fetch_pipeline(pipeline, token=None)` passes a given token explicitly and otherwise uses the ambient login (`token=True`), so an existing `hf auth login` keeps working. `meeting-scribe prefetch-models` (also `python -m meeting_scribe.cli prefetch-models`) takes `--token-stdin` (the first line of standard input, never argv or the environment; an empty line is exit 2), `--progress-json` (stdout is one JSON object per line and nothing else: `{"event":"step","model":"pipeline"|"whisper","step":N,"total_steps":2}` before each model, `{"event":"error","model","error_code","message"}` per failure, `{"event":"finished","ready":bool}` last; the message is scrubbed) and `--check` (offline, one line `{"whisper": bool, "pipeline": bool}`, exit 0 only when both are cached; it cannot be combined with the other two). The scrub hides anything shaped `hf_...` and also the exact token given. "Cached" is stricter than "a snapshot folder exists" (`base/infrastructure/hf_cache.snapshot_is_complete`): every file the model needs is present (Whisper: `config.json`, `preprocessor_config.json`, `model.bin`, `tokenizer.json`, `vocabulary.*`; pipeline: `config.yaml` plus each `$model/<path>` its config names), no symlink points at a missing blob and no `*.incomplete` blob is in the repository, because an interrupted download leaves a snapshot folder behind. The check uses `snapshot_download(local_files_only=True)` and never the network. The error texts of the worker and of the download now point at Preferences ("add your Hugging Face token in Preferences and press Download models"); the install-time text for `MODEL_UNAVAILABLE` still says how to retry the command.
- **Source**: [install spec](../specs/install.md) sections 6, AC-15, F6.4
- **Verifiable in**: `tests/unit/speakers/test_pyannote_backend.py`, `tests/unit/test_prefetch.py`, `tests/unit/test_prefetch_check_offline.py`, `tests/unit/test_cli.py`, `tests/unit/base/test_hf_cache.py`, `tests/gpu/test_offline_cached_load.py`
