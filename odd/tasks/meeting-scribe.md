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
- [ ] F1.2 `recording` domain + application (TDD): `RecordingSession` entity (states, transitions
      returning copies), errors (`RECORDING_ALREADY_ACTIVE`, `NO_ACTIVE_RECORDING`, ...), ports
      (`AudioCapture`, `AudioDeviceResolver`, `Clock`), `RecordingController` (start/stop/status →
      `Result`), session folder `<meetings_dir>/<YYYY-MM-DD_HHMMSS>/audio/`. Fakes in tests.
- [ ] F1.3 ffmpeg/PipeWire adapter (TDD): `pactl` device resolver; 2-channel 30 s WAV segments
      (ch0 mic, ch1 system monitor, ADR-002); `start_new_session=True`; stop with `q` + timeout +
      kill fallback; integration test with lavfi sources only.
- [ ] F1.4 D-Bus adapter (TDD): dbus-fast `dev.meetingscribe.Recording1` (`Start`, `Stop`,
      `State`, `ElapsedSeconds`, `StateChanged`), error mapping `dev.meetingscribe.Error.<CODE>`;
      `main.run()` becomes the asyncio service loop with SIGINT/SIGTERM handling; e2e under
      `dbus-run-session`; `docs/architecture/api-contracts.md`.
- [ ] F1.5 Packaging + runbook: systemd `--user` unit and D-Bus activation file under
      `packaging/` (not installed), runbook "record via gdbus"; real-device smoke test only with
      user consent.
### F2 GNOME extension — pending
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

Owner merges tracker #2 (`feat/foundations` → `main`). Meanwhile F1 runs on integration branch
`feat/recording` (based on `feat/foundations` until #2 lands, then retargeted to `main`).
Current: F1.1 done (PR into `feat/recording`); next F1.2 recording domain. RDD boundary for F1 = tip of `feat/foundations`
(its tree equals the reviewed F0 tree).
