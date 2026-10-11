# Feature: meeting-scribe

> ODD feature document. Engram mirror: topic `odd/meeting-scribe/tasks` (project `meeting-scribe`).
> Locator: `odd/tasks/meeting-scribe.md`.

## Objective

A local, GPU-accelerated meeting assistant for GNOME (Fedora 44, GNOME Shell 50.4, Wayland):
record from a panel button, transcribe + diarize after stop, review/name/merge speakers in a local
web UI, and generate a meeting summary with an OpenAI-compatible LLM (Ollama local / DeepSeek cloud).

## Problem / why

No GNOME extension does this; Meetily CE lacks diarization and must be compiled on Linux. A working
CLI prototype exists (`~/Documents/personal/meeting-transcriber`) but is a pair of scripts with no
service, UI, review flow or summary feature.

## Scope

Phases F0–F6 (see `docs/project/mvp-scope.md` once written). Out of scope v1: live transcription,
chat with the meeting, cloud sync/sharing.

## Constraints

- Never play audio or start a recording without asking the user first. Synthetic WAV fixtures only.
- Meeting audio/transcripts are private: never in the repo, fixtures, docs or logs.
- Cloud LLM provider is opt-in and labelled "cloud" in the UI.
- Domains must not cross (import-linter); only `meetings` may depend on other domains' `application`.
- Commits: Conventional Commits, no AI attribution / Co-Authored-By (user global rule).
- No push / PR without asking.
- 8 GB GPU: lazy model load, release after use, one GPU job at a time.

## TDD

- Mode: **strict TDD on** — source: user global config ("Strict TDD Mode: enabled") + HANDOFF §6/§7.
- Runner: `uv run pytest` (wrapped by `scripts/run-tests.sh` / `make test`).

## Delivery

- Repo: `github.com/CorsairRom/meeting-scribe` (private), created 2026-10-09 at the user's request.
- Strategy (user decision 2026-10-09): **feature-branch-chain with one integration branch per
  functionality**. Integration branch `feat/<functionality>` from `main` + draft tracker PR to `main`
  that the **owner merges**. Work-unit PRs (≤ ~400 authored lines, one unit each) target the
  integration branch; the orchestrator may squash-merge them in order. `main` only receives whole
  functionalities.
- F0: integration `feat/foundations`, tracker #2. Slices #1, #3–#12 (+ this document). The
  original commit history (hashes cited below) is preserved on local branch
  `archive/f0-chore-foundations`; slices were rebuilt from its final tree `f3af510` and the
  integration tree was verified identical (`git diff` empty except this document).
  size:exception: #1 scaffold (534 lines, tooling cannot stand without the package) and #5
  architecture + ADR-001/002 (480, cross-referenced).
- RDD: on (decided by default). Assess each work-unit commit with
  `gentle-ai review assess --committed-only` against the last reviewed boundary (first = branch point `93e5274`).

## Tasks

### F0 Foundations — branch `f0/chore-foundations`

- [x] F0.1 Scaffold + tooling: `pyproject.toml` (uv_build, src layout, py ~=3.12), package
      skeleton (`base`, `recording`, `transcription`, `speakers`, `summaries`, `meetings` each with
      `domain/application/infrastructure`), `settings.py`, `main.py`, ruff, pyrefly, pytest,
      import-linter contracts, `scripts/run-linter.sh`, `scripts/run-tests.sh`, `Makefile`,
      `.gitignore`, `.env.example`.
- [x] F0.2 Shared kernel (TDD): `base/` errors with stable `error_code`, `BaseEntity`.
- [x] F0.3 Docs: `AGENTS.md` (+ `CLAUDE.md` symlink), `PRD.md`, `ARCHITECTURE.md`, `docs/` taxonomy
      + `_templates`, `docs/project/{mvp-scope,backlog,rules-registry,doc-sync-protocol}.md`,
      error-codes catalog.
- [x] F0.4 ADRs 001–006 in `docs/decisions/` (gotchas folded into implementation notes / AGENTS.md).
- [x] F0.5 Remove `HANDOFF.md` (after user approval); full checks; RDD assess.
- [x] F0.6 STOP: present PRD, scope and ADRs to the user for review before F1. **Approved 2026-10-09** (second round, after D-Bus/GTK rework).

#### F0 review feedback (2026-10-09) — accepted changes

User decisions: speaker review happens in a **native GTK4/libadwaita app** (not a browser page), and
the extension/app talk to the service over **D-Bus session bus** (not localhost HTTP). Rationale:
everything "from GNOME"; D-Bus is unreachable from websites, so the CSRF/token machinery of the old
ADR-001 disappears. ADRs are still `proposed`, so 001 and 005 are rewritten in place with a revision note.

- [x] F0.7 Code (TDD): drop FastAPI/uvicorn/httpx, the HTTP health route and `ServerSettings`;
      transport-neutral composition root in `main.py`; update import-linter framework-forbid contract;
      `.env.example`. D-Bus adapter itself is F1.
- [x] F0.8 Docs: rewrite ADR-001 (D-Bus service) and ADR-005 (GTK4 app); ripple to PRD,
      ARCHITECTURE, AGENTS, mvp-scope, backlog, rules-registry, error-codes catalog, README.

### F1 Recording — integration branch `feat/recording` (based on `feat/foundations` until #2 lands)

Goal: the user can start/stop a recording with `gdbus call` against the running service.
Safety: tests never touch audio devices — ffmpeg adapter tests use `-f lavfi` synthetic sources;
D-Bus e2e tests run on a private bus (`dbus-run-session`). A real-device smoke test is only run
after asking the user. Installing the systemd unit / D-Bus activation file is F6 (files live in the
repo from F1).

- [x] F1.1 Settings hardening (F0 follow-ups): tilde expansion for `STORAGE_MEETINGS_DIR`; `.env`
      loaded from `$XDG_CONFIG_HOME/meeting-scribe/.env` (fallback CWD) and effective source logged;
      `log_level` Literal; shared base settings config; `detail_code` UPPER_SNAKE validation;
      `is_null` via explicit `ClassVar` marker; remove duplicate import contract and dead ruff
      ignores; portable `make help`. Branch `f1/chore-settings-hardening`.
      Route: delegated writer (8+ files). Commits `59dc9b5`, `0707115`, `9d97a7c` (+279/-41).
      TDD: settings RED `ImportError: effective_env_files` → `21 passed, 2 failed` (create_app log
      tests) → GREEN; errors/entity RED `13 failed, 13 passed` → GREEN `49 passed`. Import-linter:
      duplicate removed, every contract re-verified BROKEN→KEPT (incl. `dbus_fast` forbid).
      `.env` is an exclusive fallback: XDG file if present, else `./.env`; process env wins.
- [x] F1.2 `recording` domain + application (TDD): `RecordingSession` entity (states, transitions
      returning copies), errors (`RECORDING_ALREADY_ACTIVE`, `NO_ACTIVE_RECORDING`, ...), ports
      (`AudioCapture`, `AudioDeviceResolver`, `Clock`), `RecordingController` (start/stop/status →
      `Result`), session folder `<meetings_dir>/<YYYY-MM-DD_HHMMSS>/audio/`. Fakes in tests.
      Route: delegated writer. Commits `497352c` (entity, errors, ports — 391 lines), `99233f6`
      (controller, filesystem storage, system clock — 444). TDD: each module RED at collection
      (`ModuleNotFoundError`) → GREEN; behavioural RED on storage (existing session folder was
      tolerated) → fixed with `exist_ok=False` (GO-015). `make check` → 84 passed.
      RDD `b73812b..99233f6` (1199 lines, medium): user granted; lineage `review-9e36164f94c5bba3`
      approved + acknowledged. R3 WARNING (stop() after a crash reported STOPPED) fixed inline with
      TDD: RED `test_stop_after_crash_reports_failure_without_calling_status_first` → GREEN 85 passed.
      Technical decisions (writer's open questions): storage failure leaves state IDLE and returns
      the error (nothing was recorded); two starts within the same second collide on the folder name
      and return `RECORDING_STORAGE_FAILED` (acceptable: a restart within 1 s is not a real use case).
      Follow-up: `create_app` logs the env source via a second `effective_env_files()` call, which can
      disagree with injected settings — revisit when wiring D-Bus in F1.4.
- [x] F1.3 ffmpeg/PipeWire adapter (TDD): `pactl` device resolver; 2-channel 30 s WAV segments
      (ch0 mic, ch1 system monitor, ADR-002); `start_new_session=True`; stop with `q` + timeout +
      kill fallback; integration test with lavfi sources only.
      Route: delegated writer. Commits `94c4df0` (RecordingSettings + PactlDeviceResolver, +363/-48)
      and `8c91c07` (ffmpeg command builder + FfmpegAudioCapture, +571/-4; ~60% tests/docs).
      TDD: each module RED at collection → GREEN; `make check` → 112 passed (5.3 s), no stray
      ffmpeg (`pgrep -a ffmpeg` empty). Safety verified by the orchestrator: no test executes
      pulse/pactl (resolver uses a fake runner; the subprocess runner test monkeypatches
      `subprocess.run`); real ffmpeg only with lavfi + `-re` + `-t` under tmp_path.
      Empirical (lavfi): clean `q` → exit 0 in ~0.1 s; chunks 2ch/16 kHz/s16; ch0 = mic, ch1 =
      system verified by per-channel RMS; lavfi without `-re` writes 60 s in 0.08 s (GO-016..019).
      `MeetingScribeSettings` moved to `base/infrastructure/settings.py` so per-domain settings can
      live in their domain without importing `meeting_scribe.settings` (import contract 6).
- [x] F1.3b Capture hardening (Gentle AI review `review-cb84c2e323d8edbc`, approved with advisory
      findings; accepted because F1.4 runs everything on a single-threaded event loop): bounded wait
      after SIGKILL (R4); crash cause (exit code + ffmpeg.log tail) surfaced in `failure_reason`
      (R4/R3); log-open failure not reported as "ffmpeg not found" (R4/R3); terminated-vs-killed
      message accuracy (R2); log swallowed transition errors (R2); config-source log tied to the
      settings actually loaded (R2); document why the null session state is `STOPPED` (R2).
      Branch `f1/fix-capture-hardening`. Route: delegated writer. Commits `f431433` (+358/-21,
      items 1–5, 7; GO-020/021) and `9648fe7` (+73/-9, config source via `Settings.load()`).
      TDD RED per item (e.g. `unexpected keyword argument 'kill_grace_seconds'`, `no attribute
      'exit_detail'`, `'terminated' in '...was killed.'`) → GREEN `127 passed`; no stray ffmpeg.
      Worst-case stop = stop timeout + terminate grace + kill grace = 14 s by default.
      Known edge: if ffmpeg cannot be reaped (D-state), the handle is kept and `start()` reports
      "already running" until it is reaped — acceptable, surfaced as FAILED with the reason.
      RDD (476 lines, high): user granted; lineage `review-995050cae3b59cf8` approved + acked. Two
      warnings fixed in `cb92113`: the bounded-stop test now asserts every wait timeout (mutation
      `process.wait()` without timeout → test fails), and the env-source lookup is documented as
      best effort (nested settings resolve their own env file). Remaining suggestions (tests via
      private `_fail`, vacuous running-exit_detail test, hard-coded 14 s in a comment) left as is.
- [x] F1.4 D-Bus adapter (TDD): dbus-fast `dev.meetingscribe.Recording1` (`Start`, `Stop`,
      `State`, `ElapsedSeconds`, `StateChanged`), error mapping `dev.meetingscribe.Error.<CODE>`;
      `main.run()` becomes the asyncio service loop with SIGINT/SIGTERM handling; e2e under
      `dbus-run-session`; `docs/architecture/api-contracts.md`.
      Route: delegated writer. dbus-fast 5.2.0 (API verified via Context7 + installed source).
      Commits `c6f3489` (+55), `92815e8` (+341), `aaae8e7` (+434/-27), `5511839` (+149/-21).
      TDD: RED `ModuleNotFoundError ...base.infrastructure.dbus` / `a coroutine was expected, got
      None` (direct-called `@dbus_method` wrappers return None → logic in plain coroutines, GO-022) /
      `cannot import name 'RecordingAdapters'` → GREEN `154 passed`. E2E on a private dbus-daemon
      (temp config) with fake adapters only; orchestrator verified no test starts real adapters;
      no stray dbus-daemon/ffmpeg. `request_name` uses `DO_NOT_QUEUE`; a second instance exits.
      `run()` now blocks serving D-Bus (closes R3-001). Untested: real SIGINT/SIGTERM handler path —
      covered manually in the F1.5 runbook smoke test.
- [x] F1.4b D-Bus service hardening (Gentle AI review `review-3fd503f16952534e`, approved with
      warnings; the service runs daily, so they are accepted): crash monitor survives exceptions
      (R4-001); bus disconnect stops an active recording and exits (R4-002); shutdown stop failures
      logged at ERROR with non-zero exit (R4-003/R2-002); no concurrent controller access during
      shutdown (R3-001); SAFETY note in the e2e module names the real construct (R2-001).
      Branch `f1/fix-dbus-service-hardening`. Route: delegated writer. Commits `012f202`
      (+190/-28) and `d749217` (+153/-18). TDD RED per item (monitor task died; `stop_if_active`
      returned None; `no attribute 'start_monitor'`; `assert 0 == 2`; disconnect `TimeoutError`) →
      GREEN `159 passed`; no stray dbus-daemon/ffmpeg. Exit codes: 0 clean, 1 name owned,
      2 shutdown stop failed, 3 bus lost (GO-026). Shutdown waits for an in-flight monitor poll.
      RDD (399 lines, high): user granted; lineage `review-9a0327b7d3c2cc8e` approved + acked.
      Warnings fixed inline: R3-001 shutdown now stops even if `status()` raises (RED `OSError:
      ffmpeg.log unreadable` → GREEN 160 passed); R2-001 monitor docstring names the real halt.
- [x] F1.5 Packaging + runbook: systemd `--user` unit and D-Bus activation file under
      `packaging/` (not installed), runbook "record via gdbus"; real-device smoke test only with
      user consent.
      Route: delegated writer. Commits `df15ae3` (unit template `Type=dbus`, `RestartPreventExitStatus=1`,
      activation file → `SystemdService=`, 8 contract tests tied to `BUS_NAME`/exit codes; +106) and
      `6518856` (runbook `docs/runbooks/record-via-gdbus.md`; +115/-3). TDD RED (files missing) →
      GREEN 168 passed; `systemd-analyze --user verify` on a `/bin/true` copy: clean. Nothing
      installed, nothing run by the writer (the real-device smoke test followed with consent, below).
      Final F1 review: whole `feat/recording` vs main (4165 lines) stopped with
      `lens_context_budget_exceeded` (no authority created) → reduced to the unreviewed tail
      #27–#29 (458 lines): user granted; lineage `review-ce5f4b33c7d01c6b` approved + acked.
      Fixed: `KillMode=mixed` so systemd does not SIGTERM ffmpeg alongside the service (RED
      `KeyError: 'KillMode'` → GREEN), runbook Start reply wording, +4 s explained; regression test
      for idle shutdown with unreadable status. `make check` → 170 passed; unit verify clean.
      Real-device smoke test (user consent 2026-10-09, "record ~10 s and delete it"): service in the
      foreground on the real session bus, name owned after ~200 ms; `Start` → RECORDING; after
      10.5 s `Stop` → STOPPED; Ctrl+C → exit 0; no ffmpeg left. Chunk: 2 ch, 16 kHz, s16, 10.5 s;
      per-channel RMS mic 525 / system 552 (both non-silent); empty ffmpeg.log. Recording deleted.
### F2 GNOME extension — integration branch `feat/gnome-extension` (from `main` after #15)

Goal: start/stop recording from a GNOME Shell 50.4 panel indicator (Wayland) talking to
`dev.meetingscribe.Recording1` over the session bus; timer + status.
UX decision (user, 2026-10-09, revised the same day after real use): every click opens the
drop-down menu; Start/Stop are explicit menu actions (the first one-click-start design recorded by accident). UI strings in English via
gettext (translatable later). Environment: gjs 1.88.1, `gnome-shell --devkit` for a nested shell.
Safety: tests never touch the real session bus or the running shell; installing/enabling the
extension in the user's session, or running the nested shell, only with the user's consent.

- [x] F2.1 Pure presentation logic (TDD, `gjs -m` test runner, no Shell imports): D-Bus state →
      view model (icon, label, click action, menu items, failure/unavailable messages),
      `formatElapsed`, D-Bus error name → user message; `make test-extension` wired into
      `make check`. Branch `f2/feat-indicator-logic`.
      Route: delegated writer. Commit `51ce0d7` (+442/-8 authored, ~220 of them test lines; 22 tests). TDD with a gjs harness
      (`tests/harness.js` + explicit imports in `tests/run.js`, GO-027): RED `ImportError` → stub
      `0 passed, 1 failed` → … → GREEN `22 passed, 0 failed`. States: unavailable ○ dimmed/menu,
      IDLE/STOPPED ● start, RECORDING ■ HH:MM:SS menu+Stop, FAILED ⚠ menu+reason+Start, busy → no-op.
      `make check` runs python + extension tests.
      RDD (479 lines, medium): user granted; lineage `review-e811248a1c6784f1` approved + acked.
      Fixed inline with TDD: `fill()` expanded `$&`/`$$` from service text (RED garbled failure
      reason → replacer function → GREEN 23 passed); the harness now fails when no tests are
      registered (RED exit 0 → exit 1).
- [x] F2.2 D-Bus client (`Gio.DBusProxy`, name watching for service availability,
      `PropertiesChanged`/`StateChanged`), tested against a private bus where practical.
      Route: delegated writer. Commits `1691884` (async harness, per-test timeout; +30/-5) and
      `6a32093` (`RecordingClient`, private-bus fixture with a fake Recording1 in GJS; +679/-5,
      ~330 tests). TDD: async throw passed falsely → fixed; client RED `ImportError` → stub
      `26/11` → GREEN `37 passed` (3 green runs); no leftover dbus-daemon. Facts (GO-028/029):
      ElapsedSeconds is not in the proxy cache updates → read once via Properties.Get, then local
      monotonic clock; read `get_remote_error` before `strip_remote_error`; watcher NONE + proxy
      DO_NOT_AUTO_START so watching never launches the service. Orchestrator verified tests always
      inject the private connection.
      RDD (#34 + F2.2, 776 lines): user granted; lineage `review-27a7ef9a81ecff7e` approved + acked.
      F2.2b fixes (delegated writer): `5e9d9ea` per-recording token drops stale ElapsedSeconds seeds
      (RED `expected 5, got 100`), FAILED reads FailureReason before emitting (RED interim empty
      reason), client call timeout 20 s (> 14 s worst-case Stop); `ceb25d7` every fixture wait
      bounded at 2 s, name-lost rejects, harness cleanup registry run before exit. GREEN 41 passed
      ×3, no leftover daemons. Note: if `RECORDING_STOP_TIMEOUT_SECONDS` is raised above ~16 s the
      20 s client timeout must follow.
- [x] F2.3 Extension shell (`extension.js`, `metadata.json` shell-version 50, `stylesheet.css`):
      `PanelMenu.Button`, 1 s timer only while recording, full cleanup in `disable()` (EGO rules).
      Route: delegated writer. Commits `e8ad042` (pure `indicatorController`, 15 tests; +311) and
      `3b25864` (`extension.js`, `panelIndicator.js`, `glibScheduler.js`, metadata, stylesheet,
      static checks; +331/-5). TDD: controller RED `ImportError` → 56 passed; glue RED 7 failing
      → 70 passed. Click interception verified against GNOME 50 sources: `PanelMenu.Button` opens
      its menu from a `Clutter.ClickGesture` calling `menu.toggle()`, so the button uses
      `dontCreateMenu` + a `PopupMenu` subclass whose `toggle()` asks the controller (CR-013,
      GO-030/031). Static check: `node --check --input-type=module` via stdin (proven on broken
      sources); `make test-extension` now needs node (fails loudly if missing). NOT yet run in a
      shell — F2.4, with consent.
      RDD (#37 + F2.3, 958 lines): user granted; lineage `review-365649800a580c9f` approved + acked.
      Fixed inline with TDD: a click on an open menu now only closes it (the decision moved into the
      controller as `close-menu`; RED `expected "close-menu", got "start"`), and the orchestrator
      found `extension.js` wrapped the callback without forwarding `{menuOpen}` — fixed plus a
      static guard (RED → GREEN); a throwing tick no longer kills the GLib timer (RED `expected 2,
      got 1`). GREEN 73 passed.
- [x] F2.4 Packaging + runbook: `make extension-pack` (`gnome-extensions pack`), runbook for the
      nested `--devkit` shell and for installing in the real session (logout/login on Wayland);
      manual verification only with consent.
      Route: delegated writer. Commits `613b2a6` (`extension-pack`/`extension-install`, pack test;
      +66/-2), `f629901` (runbook `docs/runbooks/gnome-extension.md`; +172/-2), `c360b58`
      (`gnome-extensions pack` segfaults without the output dir → `mkdir -p`). TDD: pack test RED
      (target missing) → GREEN; zip = metadata, stylesheet, extension.js, lib/*.js only.
      Nested shell on GNOME 49+: `dbus-run-session gnome-shell --devkit --wayland` needs the
      `mutter-devkit` package (not installed here); extensions load from a throwaway
      `XDG_DATA_HOME` (no `--extension-dir` option). Nothing installed or run.
      RDD (#40 + F2.4, 328 lines, high) under the standing consent: lineage
      `review-a6b681e2a2ff5906` approved + acked. Applied: standing-consent row scoped to review
      consent only (R1); failing tick logged once (R4); blocking, roomier scheduler test (R3);
      forwarding guard asserts the wrapper parameter reaches the controller (mutation
      `onPrimaryClick()` → test fails); pack fixture closes the zip; runbook symptom row split.
      Follow-up RDD on #42 (219 lines, standing consent): lineage `review-0cfac5dd209f0f94`
      approved + acked. Fixed: blocking `spin()` could hang if the regression returned → guard
      timeout source (mutation dropping the source now FAILS in 13 s instead of hanging); the
      log-once flag resets after a successful tick (RED `expected 2, got 1` → GREEN 74 passed).
      Extension packed and installed (not enabled) in the user's session with consent;
      manual check pending (logout/login, enable, `make run`, click).
- [x] F2.5 Plug-and-play install (user feedback 2026-10-09, after enabling the extension and
      finding the service not running: "installing must bring everything up, including enabling the
      extension"). Pulls the install part of F6 forward: `make install` = `uv tool install` of the
      service, render + install the systemd user unit and the D-Bus activation file,
      `systemctl --user enable --now`, pack + install + enable the extension; idempotent;
      `make uninstall` reverses it. Tested with fake `systemctl`/`gnome-extensions`/`uv` on PATH
      and a temp HOME/XDG tree. Branch `f2/feat-plug-and-play-install`.
      Route: delegated writer. Commits `539b324` (install/uninstall/_common scripts, Makefile,
      18 tests with fake tools on an isolated PATH; +403/-4) and `cb25d05` (docs; +49/-32).
      `uv tool install --force --reinstall --python 3.12 <repo>`; unit ExecStart from
      `uv tool dir --bin`; `enable --now` first time, `restart` on re-install only when not
      RECORDING; extension pack+install+enable with a log-out-once hint on Wayland. TDD RED (no
      scripts) → GREEN 193 python / 74 extension. Nothing run against the real system by the writer.
      RDD (509 lines, high, standing consent): lineage `review-d68c3b4ec4043d18` approved + acked.
      F2.5b (`9b31798`, +179/-42): recording state checked BEFORE reinstalling; state read fails
      closed (UNKNOWN refuses install/uninstall without FORCE; gdbus `--timeout 5`); a failed
      service start no longer aborts (extension still installed, cause printed, exit 1); honest
      PATH-isolation tests with decoy tools. RED 14 → GREEN 228 python / 75 extension.
      RDD on F2.5b (226 lines, high, standing consent): lineage `review-132aff3a2e6ad58c` approved +
      acked. F2.5c (`ac96995`, +97/-13): state re-checked right before restart (a recording started
      during the reinstall is never killed; restart skipped with a warning), `FORCE=1` overrides only
      `unknown` for install, explicit IDLE/STOPPED/FAILED matching (anything else → unknown), real
      PATH assertion, separate daemon-reload cause. RED 6 → GREEN 237 python / 75 extension.
      RDD on F2.5c (115 lines, standing consent): lineage `review-65c7e305b205e63c` approved + acked.
      Fixed inline: `FORCE=1` now also restarts an active service whose state stays unknown (the
      documented wedged-service remedy), never a recording (RED 2 → GREEN 240 python).
- [x] F2.6 Menu-only click (user feedback 2026-10-09 after a stray click recorded 4 s): every
      state opens the drop-down; Start/Stop are explicit menu actions. Inline TDD in a worktree:
      RED 5 failing → GREEN 75; guard test "no state starts a recording on a plain click".
      Merged as #44 (78 lines, under budget). Supersedes the 2026-10-09 one-click-start decision.
- [x] F2 real-session verification (user, 2026-10-09): `make install` run by the orchestrator with the
      user's go (exit 0: uv tool at `~/.local/bin/meeting-scribe`, unit enabled + active, activation
      file installed, extension reinstalled + enabled; service answered `IDLE`). After one
      logout/login the user confirmed the menu-only indicator and recorded successfully.
- [x] F2.7 Installer polish (Gentle AI review `review-a96ff3aaa6a272a2`): versioned tool install with
      an atomic switch so a recording that starts mid-reinstall never runs on a swapped venv; extension
      pack/install failures keep the summary; log-out hint and start-failure cause reflect the real
      state; one FORCE contract shared by install/uninstall; escaped ExecStart rendering + assertion;
      tests for the untested reinstall branches; CR-013 notes the toggle hook is now a kept extension
      point. Branch `f2/fix-installer-polish`.
      Route: delegated writer. `fbe11c4` (+403/-50): versions under `~/.local/share/meeting-scribe/
      versions/<ts>-<pid>/{tools,bin}` via `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` (verified with uv 0.10.9),
      `current` symlink switched with `mv -T`, ExecStart stable at `current/bin`, prune after a
      successful restart only, migration uninstalls the legacy uv tool after the new unit is active;
      extension failures keep the summary; truthful hints; shared FORCE scope; ExecStart rendered in
      bash and checked with `grep -Fx`. `7578713` CR-013 note. RED 49 → GREEN 88 install tests.
      Orchestrator fix: uninstall removed the whole `~/.local/share/meeting-scribe` (future caches)
      → now only `versions/` + `current` (RED test → GREEN; `rmdir` added to the sandbox whitelist).
      `make check` → 262 python / 75 extension.
      RDD (442 lines, standing consent): `review-3c906d6d33a4ced9` approved + acked. F2.7b
      (`e71917e`, +206/-37): rollback to the previous version (or the legacy unit on migration)
      when the new one fails to start; switch only after the unit render and executable are
      verified; explicit legacy-tool detection; behavioural FORCE tests. RED per item → GREEN
      269 python / 75 extension. RDD `review-b082c4e0acfdc610` approved + acked. Accepted as-is
      (single-user edge cases, noted for F6): restoring a hand-edited custom unit on a failed
      first upgrade, rollback summary wording/README precision, a test for rollback with an
      inactive service.
      Live migration (user's go, 2026-10-09): `make install` exit 0 → version
      `20261009173638-2310554`, `current` → that version, ExecStart `…/current/bin/meeting-scribe`,
      legacy `~/.local/bin/meeting-scribe` removed, service active (`IDLE`), extension enabled.
      Cosmetic follow-up: silence uv's "bin is not on your PATH" warning for versioned bins.
### F3 Processing — integration branch `feat/processing` (from `main` after #35)

Goal: on Stop, the service automatically transcribes (faster-whisper large-v3-turbo, CUDA float16)
and diarizes (pyannote community-1) the recording, extracts a voice sample per speaker, labels the
user's own voice "Me" from the mic channel (ADR-002), stores results in the meeting folder
(ADR-004) and sends a desktop notification ("Done: N speakers"). One GPU job at a time; models are
never resident together (ADR-003). Pending jobs resume at service start.
Safety: tests use synthetic audio only and never load models (adapters behind ports, GPU tests
opt-in and skipped by default); real-recording validation only with the user's consent, on their
own meeting, without reading its content into the repo or logs.

- [x] F3.0 Design spec `docs/specs/processing.md` + ADR-007 (GPU work in a short-lived worker
      subprocess per job, so VRAM is fully released) after mapping the prototype and verifying
      faster-whisper / pyannote.audio 4 APIs. Branch `f3/docs-processing-design`.
      Route: delegated writer (opus). `59b34d4` spec (857 lines) + backlog/README; `89f005b` ADR-007 +
      ARCHITECTURE. Revision vs plan: one worker process per GPU STAGE (CTranslate2 manages its own
      VRAM; only process exit guarantees release). Transcribe the channel mix, diarize the system
      channel, "Me" by per-frame energy dominance (6 dB) with bleed estimate, "Unknown" when
      ambiguous; ML deps in an `ml` extra kept out of the service by an import contract.
      User approved ADR-007 (now active) and all 8 product recommendations on 2026-10-09: process
      FAILED captures; ignore folders without meeting.json; preempt processing while recording and
      resume after; in-person meetings diarize the mic without "Me"; CUDA required unless CPU set
      explicitly; `Meetings1.Reprocess` in F3; models downloaded during `make install`, worker
      offline afterwards; keep "Unknown".
- [x] F3.1 `meetings` domain: Meeting entity + processing state machine, versioned `meeting.json`
      repository (folder per meeting), job queue + single worker orchestration, resume at start.
      Evidence — F3.1a: `665c812` entity + state machine + timeline/fingerprint,
      +657 incl. ~350 tests; `8f7b926` filesystem repository meeting.json v1, atomic `Path.replace`,
      unknown keys preserved, UNSUPPORTED for newer schemas, null object for missing, path-traversal
      ids → null; RED `ModuleNotFoundError` per module → GREEN 332 python. Open for F3.1b: resume of a
      job stored in DIARIZING restarts at transcribe → controller needs a resume transition).
      F3.1a integrated (#53, #54; review `review-3204a2c798a1571a` approved). F3.1b (delegated):
      `d6297d0` save identity check + directory fsync; `9e81ef3` ProcessingController (enqueue with
      RecordingInfo, resume, reprocess, preempt/release, shutdown) + FIFO single-worker loop +
      `Meeting.resume`; `1e94014` subprocess WorkerRunner, JSON-lines protocol v1 (`event` key; job
      spec on stdin; started/cache_hit/progress/stage/result/error), timeout/crash/protocol errors,
      SIGTERM→SIGKILL on the process group, worker entry-point skeleton without ML imports;
      `be6105f` ProcessingSettings + WavChunkInspector. RED per new module → GREEN 414 python / 75
      extension; queue/preempt tests mutation-checked; no stray workers. Integrated #55–#58; review
      `review-a90aa9ce9149e00c` approved (2422 lines). F3.1c fixes (delegated): `7f0b598` bare-id
      check on save, dir-fsync errors tolerated, truncated WAV headers mapped; `320ec1a` a failed save
      never strands a job (record failure, else requeue; give up in memory after 3); `bac0e10` bounded
      wait after a worker result + spawn-failure cleanup; `f008a99`/`904dcf7` scoped ruff ignores,
      `JobOutcome` enum, settings as single source of processing defaults. GREEN 437 python.
      Integrated #59; review `review-f9c413cad1c9ae97` approved. F3.1d (`fd6c6d5`, +379/-35): storage
      retries back off (`PROCESSING_STORAGE_RETRY_DELAYS_SECONDS`, default 2/8/30 s, budget = delays+1
      runs), `storage_failed` event after the budget, read failures `UNLOADED` (retried) vs
      `UNREADABLE` (corrupt/newer schema, reported, not retried, new `MEETING_CORRUPT`), result kept
      when the deadline passes during the post-result grace. GREEN 454 python. Pending for the user:
      `.env.example` is blocked by the permission settings for agents → add
      `PROCESSING_STORAGE_RETRY_DELAYS_SECONDS=[2,8,30]` by hand.
- [x] F3.2 `transcription` domain + faster-whisper adapter (CUDA lib preload with logging, WAV
      decode without PyAV, word timestamps, params-keyed cache).
      Evidence — integrated #60 (F3.1d, review `review-38ba44f658d2bbc7` approved). F3.2 (delegated):
      `c5ea0f3` review fixes (deduped delayed retries, pure `_can_start` + `_promote_due_retries`,
      `storage_failed` carries id + code only); `01ef76a` transcription domain, params, cache key
      (params + audio fingerprint), TranscriptionSettings; `4268263` faster-whisper worker stage (lazy
      imports, CUDA preload 17 libs logged, cuda default → `GPU_UNAVAILABLE` without CPU fallback,
      wave+numpy decode, per-segment progress, cache hit), `ml` extra (faster-whisper 1.2.1,
      ctranslate2 4.8.2, cublas 12.9.2.10, cudnn 9.27.0.42; no torch yet), 2 new import contracts
      (8 kept; both proved to bite). GREEN 517 python (2 GPU deselected). Opt-in GPU test on synthetic
      audio: VRAM 457 → 2516 → 457 MiB after exit; 4.2 s first run (load 2.5 s), 0.2 s cache hit
      (A-1 partly, A-3/A-4 confirmed for Whisper). New env vars for the user's `.env.example`:
      TRANSCRIPTION_MODEL/LANGUAGE/COMPUTE_TYPE/CPU_COMPUTE_TYPE/BEAM_SIZE/VAD_FILTER/
      CONDITION_ON_PREVIOUS_TEXT.
- [x] F3.3 `speakers` domain + pyannote adapter (exclusive diarization), word→speaker assignment,
      utterance grouping, renumbering, "Me" by per-turn mic/system energy dominance, voice samples.
      F3.3a (delegated): `3952db1` lazy worker stage build (refused invocations import no adapters
      or numpy) + JsonWordsCache and load-audio failure tests; `3f521f2` assignment (max overlap,
      else nearest by midpoint), grouping (gap 5.0 s), numbering, rendering, SpeakersSettings;
      `6525895` "Me" by per-frame energy dominance (6 dB, noise margin 10 dB, bleed estimate, min
      turn 0.5 s, "Unknown" when ambiguous), in-person fallback (<1 % system speech), voice sample
      selection (3 clips, 15 s, 1.5–6 s) — pure Python over precomputed frame energies (speakers
      domain never imports numpy). RED per module → GREEN 587 python; bleed mutation (k=0) fails 3
      tests. New env vars SPEAKERS_* for the user's `.env.example`.
      F3.3a integrated (#64–#66; review `review-2e02a0efe5b00027` approved). F3.3b (delegated; the
      first run died with the previous session and was resumed): `7472173` settings/Me-turn fixes from
      the F3.3a review; `82b2803` pyannote worker stage (pyannote.audio 4.0.7, torch 2.14.1 from PyPI;
      `PYANNOTE_METRICS_ENABLED=false` (telemetry is ON by default, A-8); gated/401/403/no-token →
      `DIARIZATION_MODEL_ACCESS_DENIED` with `hf auth login` hint (A-11); OOM → `GPU_OUT_OF_MEMORY`;
      quiet progress hook (A-12)); `d984381` samples + `transcript.md` + `utterances.json`;
      `e47daa9` contract-7 row (caught by the new drift test after rebasing onto #67).
      TDD DEVIATION: the writer wrote code before tests and recorded RED afterwards by stashing `src`
      (14 failures / collection errors) — not strict TDD; noted for future briefs. GREEN 672 python.
      The model was NOT downloaded/loaded (user's HF login not authorized yet): the real GPU test
      `tests/gpu/test_diarize_worker.py` (A-5) runs in F3.5 with consent.
- [x] F3.4 Wiring (F3.4a + F3.4b done): Stop enqueues processing; D-Bus `Meetings1` (list/get, processing state,
      `ProcessingFinished`); desktop notification; extension shows processing state.
      F3.3b integrated (#68–#70; reviews d2b70651, 9f53b245, e9dfff1c approved; deps split from code to
      fit the lens budget). F3.4a (delegated, test-first enforced): `7447def` diarization review fixes
      (bounded energy memory 38.5→<4.8 MB, staged sample/transcript writes `.assembling/` + atomic swap,
      final progress kept, PYTHONPATH-independent subprocess test); `e29d1cf` RecordingListener +
      handoff (Stop/crash → enqueue + release, Start → preempt), ProcessingConfig built from settings
      with a wiring test, processing loop thread started/stopped by serve() (resume at start, 20 s
      bounded shutdown); `b2d1d50` Meetings1 (List/Get/Reprocess, ActiveMeetingId/ActiveState/
      QueuedCount, ProcessingStateChanged/ProcessingFinished), Notifier + org.freedesktop.Notifications
      adapter ("Meeting processed: N speakers"), private-bus e2e with fake worker + fake notifier.
      GREEN 732 python / 75 extension; no stray daemons/workers. Open (F3.5): a stopped session that
      cannot be enqueued (storage error) is only logged; loop outliving the shutdown bound exits 0.
      F3.4a integrated (#71–#73; reviews 1a45351a, 03eb3d7c, 1117f150 approved). F3.4b (delegated):
      `5a3d14d` outputs committed as one set (backup dir + manifest, `.commit.done` as commit point,
      interrupted commits rolled back; 23 failure-injection cases, 14 RED before), handoff release in
      `finally`, unexpected crash → FAILED announced, failed ProcessingFinished asserted e2e;
      `15f9618` extension MeetingsClient + menu processing lines (running stage, queued count, last
      result "Last meeting processed: N speakers" / "no speech detected", FAILED → Reprocess action;
      panel shows "…" while processing; click still only opens the menu). RED observed per behavior
      (client RED via stub swap after it was written first). GREEN 760 python / 104 extension.
- [x] F3.5 Install/packaging update (ML deps in the versioned install), runbook, real-recording
      validation with consent.
  - [x] F3.5a Code + docs (delegated writers; route: delegated direct, 2+ non-trivial files).
        `a2993f6` review follow-ups (enqueue-failure notification, exit 4 = processing outlived the
        shutdown bound, extension stale last result / proxy error log); `1202d85` `uv tool install
        "meeting-scribe[ml] @ <repo>"` + `meeting-scribe prefetch-models` (console script
        `meeting_scribe.cli:run`; pyannote first, then Whisper; worker env forces HF_HUB_OFFLINE=1);
        `d497289` `docs/runbooks/processing.md`; `d12ca59`/`f15a006`/`62914be` prefetch skipped after
        rollback, bounded by MEETING_SCRIBE_PREFETCH_TIMEOUT (3600 s, validated), explicit outcome
        status, accurate messages, CLI wiring test; KillMode=mixed kills a stray worker before restart
        (man systemd.kill, GO-026). RED observed per behavior (commit-point matrix and CLI wiring are
        characterization). GREEN 802 python / 108 extension, 8 contracts kept. Reviews
        review-97213a81aa56a412, review-dbd9549303e5d65f, review-74262dea92e82e8b approved and
        acknowledged. Integrated #76–#78; origin/feat/processing tree == local.
        Follow-ups (not fixed, avoid review loop): exit 137 may be an OOM kill rather than the
        timeout (summary would say "timed out"); summary `*)` branch prints "downloaded" for an
        unknown status; prefetch hint hard-codes setting names.
  - [x] F3.5b Real validation with user consent: `hf auth login` + accept community-1 terms,
        `make install` (prefetch), `uv run pytest -m gpu`, one real meeting checked by states/counts
        only.
    - [x] `make install` (user go, 2026-10-09): exit 0, version 20261009204854-3729151, service active,
          extension enabled, "Models ready: 2 of 2" with the stored login (no new login needed).
          Measured: 7.1 GB per version, models 1.6 GB (turbo) + 32 MB (community-1).
    - [x] `uv run pytest -m gpu`: first run RED, diarization chose `mic` because the synthetic
          system channel had a tone in every frame (10th-percentile floor = the tone, OQ-4 reads it
          silent); fixture fixed with pauses (`c9b899d`), GREEN: community-1 peak 517 MiB over 13 MiB,
          load 4.0 s, VRAM back to baseline; Whisper peak 2075 MiB. Review review-3aca299aef832622
          approved. Integrated #79. Follow-up: no unit test pins the continuous-sound edge of OQ-4.
    - [x] One real meeting processed (user-run, 2026-10-09): the extension menu showed the stages and
          "Last meeting processed: 1 speaker" (DONE); checked from the user's screenshot only, no
          content opened. User feedback recorded in the backlog "Later improvements" (storage layout,
          readable names, audio retention, DB only if needed, cross-meeting voices, legacy import) and
          F5 (menu opens the summary, not the transcript). Decision: F3 closes as is, then F4.
- [x] F3 docs gate: read-only audit (no P1/P2 left after #80), `make check` green, language scan clean
      (no voseo in code, docs or commits; only neutral Spanish fixture words for non-ASCII tests).
      Review review-9c4fa0825fe1a866 approved. Tracker #52 marked ready for the owner.
### F4 Review app — done (tracker #82 merged 2026-10-11)
Spec: `docs/specs/review-app.md` (approved 2026-10-11). TDD: strict, runner `uv run pytest` / `make check`
(source: AGENTS.md). Route: delegated direct (one writer per task, 2+ non-trivial files each).
- [x] F4.0a Language scan added to the "Integration PR docs gate" (#81; user rule: Spanish only in chat).
- [x] F4.0b Design spec (delegated writer, `d6c1b72`); product decisions OQ-1..OQ-6 answered by the owner one
      at a time: reset on reprocess; "Me" target-only, "Unknown" mergeable; duplicate names rejected with
      merge-or-rename; unmerge included; no name suggestions; menu-only entry (notification action and name
      suggestions moved to backlog "Later improvements").
- [x] F4.1 `speakers`: speaker records, name rules, merge/unmerge, effective speakers, transcript rendering.
      Delegated writer; ~1230 authored lines (split by behavior, over the heuristic). RED observed per unit.
      Gaps resolved by the orchestrator from the owner's decisions: unmerge reverts an adopted name (kept if
      renamed since), rejects a name clash; renaming a merged-away speaker rejected. Review
      review-0200f4479043c15b approved with 2 warnings (chained adoption, inconsistent persisted state) fixed
      in `57ba9b4`, review review-11be7b875a8e831a approved. Integrated #84-#86. GREEN 853 python / 108 gjs.
      Follow-up (suggestion): validate_state does not reject a self-adoption record (unreachable by commands).
- [x] F4.2 `meetings`: review section in meeting.json, reset on reprocess, lock, ReviewController.
      Delegated writer; ~860 authored lines (~70% tests). RED observed (repository round trip is
      characterization). Spec gaps accepted: GetSpeakers also requires DONE + lock; repair_stale logs id+code
      and continues. Review review-7ca4c6939d1df53d approved (1 warning: unmapped error on the repair path,
      fixed in `3e6a51e`), review-514d18b1ad9860ed approved. Integrated #87. GREEN 894 python / 108 gjs.
- [x] F4.3 `Meetings1` review members + `SpeakersChanged`, api-contracts (first), error catalog, e2e.
      Delegated writer; ~800 authored lines. RED observed per behavior. Gaps accepted: sample errors live in
      meetings (read through a meetings port); 1 MiB sample refusal; SpeakersChanged also fires when a job
      ends DONE. Review review-9d039d6d0c138290 approved. Integrated #88-#89. GREEN 916 python / 108 gjs.
      Follow-ups (suggestions): bounded read instead of stat-then-read for samples; test that a FAILED job
      emits no SpeakersChanged; repair_stale runs synchronously at start (latency grows with stale meetings).
- [x] F4.4a `review-app/` scaffold, core models/ports, import-linter roots and contracts (A-6 verified:
      import-linter 2.15 supports two root packages; 12 contracts kept). Review review-c8b314a2c4be0c97 (4 lenses)
      approved; 2 warnings fixed in `885ef7e` (review-0ffd2cc283da1af3 approved). Integrated #90.
- [x] F4.4b core presenters (list, review, rename, merge, playback). First CRITICAL finding of the project
      (late reply applied to another meeting) fixed by one bounded correction `bb1dba3`, validated by the
      provider targeted validator; review review-562d369f1d637d8e approved. Integrated #91-#92. GREEN 1047
      python / 108 gjs. The `_moment` OverflowError follow-up was fixed in F4.5 (`ebcfeeb`).
- [x] F4.5a GTK ui: application, window, sidebar, transcript, D-Bus gateway; session/signal/launch helpers in
      core; GO-050 (ui excluded from pyrefly), GO-051 (GTK/PyGObject facts). Integrated #93.
- [x] F4.5b GTK ui: speaker rows, dialogs, unmerge, media player, banners. Review review-b65cfc0c45f369eb
      (4 lenses) approved with warnings (markup in names, date fallback, flaky e2e wait, weak gi probe) fixed in
      `f413761`; review-7659042cad0c69c2 approved. Integrated #94. GREEN 1095 python / 108 gjs. Widgets and
      playback not run on a display (manual check in F4.8). Follow-ups for F4.6 first commit: e2e client has no
      deadline (may hang if fewer signals), no test for the gateway INVALID_REPLY path (and KeyError/
      AttributeError not caught), sidebar timestamp() may raise before the fallback.
- [x] F4.6 packaging: launcher (pinned /usr/bin/python3), desktop entry written only after a successful switch
      and removed on rollback to a pre-F4 version, Exec quoted per spec, uninstall removes only what install
      created, runbook `docs/runbooks/review-app.md`, GO-052. Integrated #95 (+ hardening in #96).
- [x] F4.7 extension "Review last meeting" item: only in IDLE/STOPPED/FAILED, hidden while busy, failure
      notification (GO-053). Reviews review-f12a84b35a5dde44 (4 lenses) and review-e21ff1119217dad3 approved.
      Integrated #96. GREEN 1123 python / 122 gjs. Follow-ups (docs gate): GO-052 still says REVIEW_APP_PYTHON
      is test-only (it is also the run-review-app override); install test comments/asserts (rollback summary
      comment, Exec parser `%%`, rollback-keep assertion, make dry-run return code).
- [x] F4.8 validation with the owner's go (2026-10-11): `make install` (version 20261011162806-703787, review app
      ready, Meetings1 review methods confirmed by introspection); the owner opened the app from the menu and
      reported it works. Docs gate: read-only audit (5 P1, 5 P2 doc drifts, no code issue) fixed in `6718079`;
      language scan clean; `make check` green (1123 python / 122 gjs, 12 contracts). Tracker #82 marked ready.
### F5 Summaries — in progress (integration `feat/summaries`)
TDD: strict, runner `uv run pytest` / `make check` (source: AGENTS.md). Route: delegated direct.
- [x] F5.0a Phase start: F4 done, F5 in progress (mvp-scope, AGENTS, backlog, README, spec statuses).
- [x] F5.0b Design spec `docs/specs/summaries.md` (approved) + ADR-008 (Decisions section). Owner decisions
      OQ-1..OQ-8 (2026-10-11): Decisions section; manual button only; names sent as written to the cloud;
      meeting language, technical terms never translated; one backup on regenerate, stale on reprocess; default
      `qwen3.5:9b`; menu opens summary.md in the default app; ONE button with SUMMARIES_PROVIDER, "(cloud)"
      label and per-request confirmation when the provider is the cloud (safety rule 3).
- [ ] F5.1 `summaries` domain: request, ports, chunker, prompt builder, renderer, fingerprint, errors (~350)
- [ ] F5.2 `GenerateSummary` use case: single pass / map-reduce, cancel, progress, validation (~300)
- [ ] F5.3 settings + OpenAI-compatible provider + Ollama model release + `openai` dependency (~400)
- [ ] F5.4 `meetings`: SummaryInfo, SummaryController, GpuLease, backups, stale (~400)
- [ ] F5.5 `Summaries1` D-Bus + wiring + api-contracts + e2e (~400)
- [ ] F5.6a review app core: summaries gateway, presenters (~400)
- [ ] F5.6b review app ui: summary view, button, confirmation, progress (~350)
- [ ] F5.7 extension "Open last summary (date)" (~250)
- [ ] F5.8 install Ollama/model check (warn only), runbook (~250)
- [ ] F5.9 validation with consent (local run; cloud only on explicit go), docs gate
### F6 Install — pending

## Route log

| Task | Route | Trigger evidence |
|---|---|---|
| Mapping | delegated (2 Explore mappers, parallel) | prototype = 5 files; conventions = 3 repos |
| F0.1 + F0.2 | delegated (1 writer) | 2+ non-trivial files (pyproject, settings, main, kernel, tests) |
| gitignore fix | inline | one mechanical file, root cause already understood |
| F0.3 + F0.4 | delegated (1 writer, ran in parallel with the code review — disjoint files) | 30+ doc files |
| review suggestions | inline | two mechanical one-line doc edits |
| RDD consent | standing | user instruction 2026-10-09: always answer `granted` to Gentle AI review-consent envelopes in this repo. Scope: review consent ONLY — it does not cover recordings, installing/enabling the extension, running a nested shell, product decisions or pushes; those still need explicit consent. |

## Accepted deviations from HANDOFF (verified by mapping)

- Per-domain settings with `env_prefix` exist in no reference repo: introduced here as a new convention.
- `create_app` pattern comes from qforest-chatbot (qforest-be is Django, not FastAPI).
- Dev tools live in the `quality-assurance` optional extra (chatbot style), not dependency groups.
- donde-compro templates are Spanish and have no PRD template: translated to English; PRD modelled on its root `PRD.md`.
- `make install` reserved for F6; dev bootstrap is `make setup`.
- ~~Service binds `127.0.0.1:8765`~~ → superseded in F0 review: D-Bus session bus, no HTTP.

## Progress / evidence

- `git init -b main`, empty root commit `93e5274`, branch `f0/chore-foundations` created.
- F0.1 → commit `8fccc8a` `chore: scaffold package layout and quality tooling`.
  TDD: settings RED `ModuleNotFoundError: meeting_scribe.settings` → GREEN 4 passed; logging + health
  RED 2 collection errors → GREEN 6 passed.
- F0.2 → commit `e94370a` `feat(base): add shared kernel errors and base entity`.
  TDD: errors + entity RED 2 collection errors → GREEN 14 passed.
- import-linter: one violating import per contract → `0 kept, 6 broken`; reverted → `6 kept, 0 broken`.
- Parent spot check: `make lint` (ruff OK, format OK, pyrefly 0 errors, 6 contracts kept),
  `make test` → `14 passed, 1 warning` (Starlette deprecation suggesting `httpx2`).
- Resolved: fastapi 0.143.0, pydantic 2.14.0, pydantic-settings 2.15.0, returns 0.29.0, ruff 0.16.10,
  pyrefly 1.3.2, import-linter 2.15, pytest 9.1.1.
- RDD assess `93e5274..f9cc17b` (committed-only): risk `high` (executable shell scripts) → review due.
  User granted consent. 4-lens review (lineage `review-a8f49c618f452332`) → **approved**, acknowledged
  (authority burned). 21 advisory findings, none blocking. Reviewed boundary advances to `f9cc17b`.
- Review surfaced a real defect: unanchored `.gitignore` rule `meetings/` ignored
  `src/meeting_scribe/meetings/` → package never committed (lint-imports passed only because files
  existed on disk). Fixed in `d4c5a06` `fix: track meetings package ignored by unanchored gitignore rule`
  (`/meetings/` root-anchored; also ignore opus/mp3/m4a/webm). Verified with `git check-ignore` (no
  match) and `make imports` → 6 kept.

- F0.3 → `4784a83` `docs: add prd, architecture, agent guide and docs taxonomy`; F0.4 → `b6c2694`
  `docs(adr): record initial architecture decisions 001-006` (all ADRs `status: proposed`).
  Structural readback: 78 relative links / 31 files, 0 broken; `CLAUDE.md -> AGENTS.md` symlink
  (mode 120000); no real session folder names in tracked files; `make check` → 14 passed.
- RDD assess `f9cc17b..b6c2694`: risk `medium`, `slice_budget_reached` (3154 lines) → review due.
  User granted consent. 1 consolidated lens (lineage `review-4b8bec88bf9ed778`) → **approved**,
  acknowledged. 2 suggestions applied in a follow-up docs commit (catalog example returned
  `Failure` instead of `raise`, per CR-004; backlog synced). Reviewed boundary → `b6c2694`.
- F0.7 → `01300e8` `refactor: drop http scaffold in favor of transport-neutral composition root`
  (delegated writer). TDD: `test_main.py` RED `ImportError: cannot import name 'Application'` →
  GREEN. import-linter accepts non-installed forbidden externals (`gi` violation → BROKEN, verified);
  all 6 contracts bite. `make check` → 17 passed, httpx warning gone.
- F0.8 → `b8e6b97` `docs(adr): switch to d-bus transport and native gtk review app` (delegated
  writer): ADR-001 → `001-thin-gnome-extension-dbus-service.md`, ADR-005 →
  `005-review-ui-native-gtk-app.md` (still `proposed`, revision note); CR-011, GO-014, AP-004 added;
  0 broken links. Verified facts: dbus-fast high-level service API (context7); system python3 3.14 +
  python3-gobject 3.56 / gtk4 4.22 / libadwaita 1.9 / gjs 1.88; venv is 3.12 → GTK app runs on
  system python.
- RDD assess `b6c2694..01300e8`: `medium`, `slice_budget_reached` → user granted; 1 lens (lineage
  `review-a40bb04f92a710e9`) → **approved**, acknowledged. Applied R3-002 in `a13dfc4`
  `test: clear settings cache on fixture teardown`. R3-001 (run() returns immediately) is intended
  until F1 makes it the D-Bus event loop — no systemd unit may point at it before then.
- RDD on `a13dfc4..4d49647` (lineage `review-c7d540b2367a4dba`) → approved, acknowledged; 3 doc nits
  fixed in `c3e3c1f` (assessed `under_budget`, pending in next slice).
- 2026-10-09 user approved PRD, scope and ADRs → ADRs 001–006 `active`; `HANDOFF.md` deleted (content
  folded into PRD, ARCHITECTURE, AGENTS, rules-registry and ADRs; never committed).

## Follow-ups (carry into F1, from review advisory findings)

All resolved in F1.1 except: `run()` must become the D-Bus event loop before any systemd unit uses
it (F1.4). Items below kept for traceability.

- `STORAGE_MEETINGS_DIR=~/...` not expanded → expanding validator (TDD).
- `.env` resolved relative to CWD: under systemd it is silently ignored → load from a fixed XDG
  config path (e.g. `~/.config/meeting-scribe/.env`) and log the effective config source.
- `log_level` → `Literal`/validator.
- `run()` must become the long-running D-Bus event loop before any systemd unit uses it (R3-001).
- `BaseEntity.is_null` relies on `_Null` class-name prefix → explicit `ClassVar` marker.
- `detail_code` not validated as UPPER_SNAKE.
- Duplicated `SettingsConfigDict` per class → shared base settings class.
- Duplicate import-linter restriction (base → settings/main in two contracts); ruff dead ignores.
- `make help` depends on `rg`/`sd` → use portable awk.
- Resolved in F0.7: port bound and e2e health isolation (HTTP removed), root-logger leak
  (`restore_root_logger` fixture), httpx warning.

## Next step

F0 merged to `main` (#2, squash). F1 complete on integration branch `feat/recording`: F1.1–F1.5
integrated (#14, #16–#31; packaging/runbook #28–#30, smoke test #31). Final tail review
`review-0b5874936265a288` approved. Tracker #15 is ready for the owner to merge into `main`.
F1 merged to `main` (#15, merge commit 7301d96). F2 started on `feat/gnome-extension`; current: F2.4 done; F2.1–F2.3 integrated (#33–#34, #36–#40); F2 complete and verified in the real session (#33–#48); tracker #35 ready for the owner. Next: F3 Processing.
