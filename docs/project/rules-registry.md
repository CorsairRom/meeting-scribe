---
type: registry
status: living  # ALWAYS 'living' — this doc is never "finished"
last_updated: 2026-10-09
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

### GO-002 — torch cu130 and ctranslate2 cu12 coexist
- **Date**: 2026-10-09
- **Status**: active
- **Detail**: `pyannote.audio` pulls torch `+cu130`, which supports Blackwell (sm_120). CTranslate2 uses its own cu12 libs (GO-001). Both work in one process; do not try to unify them.

### GO-003 — faster-whisper 1.2.1 with PyAV 19 is broken
- **Date**: 2026-10-09
- **Status**: active
- **Symptom**: `TypeError` from `av.open(..., metadata_errors=...)`.
- **Solution**: decode our own 16 kHz s16 WAV chunks with stdlib `wave` + numpy and pass a float32 array to `model.transcribe`.

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
- **Detail**: `serve` waits for `stop` or `bus.wait_for_disconnect()` (dbus-fast 5.2.0 raises or resolves when the connection closes). A lost bus is logged at ERROR, the active recording is stopped, and the exit code is 3. Exit codes: 0 clean, 1 name taken, 2 shutdown stop failed, 3 bus disconnected (disconnect wins over a stop failure). `_publish` catches emit errors so a dead bus cannot abort shutdown.

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

---

## Statistics (keep updated)

| Category | Active | Superseded | Deprecated |
|---|---|---|---|
| BR | 4 | 0 | 0 |
| CR | 13 | 0 | 0 |
| GO | 36 | 0 | 0 |
| AP | 4 | 0 | 0 |

**Last rule added**: GO-036 — 2026-10-09
**Next available IDs**: BR-005, CR-014, GO-037, AP-005

---

## How to deprecate or supersede a rule

Mark the old rule `Status: superseded` with `Superseded by: <ID> (date)` and the reason; create the new rule
with `Supersedes: <ID>`. Never delete rules.
