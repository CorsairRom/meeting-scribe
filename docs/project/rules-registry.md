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
- **Verifiable in**: `docs/architecture/api-contracts.md` (from F1); e2e tests under `dbus-run-session`

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
| CR | 11 | 0 | 0 |
| GO | 21 | 0 | 0 |
| AP | 4 | 0 | 0 |

**Last rule added**: GO-021 — 2026-10-09
**Next available IDs**: BR-005, CR-012, GO-022, AP-005

---

## How to deprecate or supersede a rule

Mark the old rule `Status: superseded` with `Superseded by: <ID> (date)` and the reason; create the new rule
with `Supersedes: <ID>`. Never delete rules.
