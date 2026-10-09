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
UX decision (user, 2026-10-09): idle `●` → one click starts; while recording `■ HH:MM:SS` → click
opens a menu with status and "Stop recording" (no accidental stop). UI strings in English via
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
### F3 Processing — pending
### F4 Review UI — pending
### F5 Summaries — pending
### F6 Install — pending

## Route log

| Task | Route | Trigger evidence |
|---|---|---|
| Mapping | delegated (2 Explore mappers, parallel) | prototype = 5 files; conventions = 3 repos |
| F0.1 + F0.2 | delegated (1 writer) | 2+ non-trivial files (pyproject, settings, main, kernel, tests) |
| gitignore fix | inline | one mechanical file, root cause already understood |
| F0.3 + F0.4 | delegated (1 writer, ran in parallel with the code review — disjoint files) | 30+ doc files |
| review suggestions | inline | two mechanical one-line doc edits |
| RDD consent | standing | user instruction 2026-10-09: always answer `granted` in this repo |

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
F1 merged to `main` (#15, merge commit 7301d96). F2 started on `feat/gnome-extension`; current: F2.4 done; F2.1–F2.3 integrated (#33–#34, #36–#40); pending: manual check in a shell (needs consent).
