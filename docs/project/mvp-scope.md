---
type: scope
status: draft
last_updated: 2026-10-09
ai_instructions:
  - "This is the contract of what IS and IS NOT implemented in the current release (v1)."
  - "If a deliverable is not in 'IN SCOPE', do NOT build it now even if it appears in the PRD."
  - "When you complete a deliverable, update its status and add date + commit."
  - "If the PRD and this doc conflict, THIS doc wins for v1."
  - "Work phase by phase. The phase table below is machine-checked by tests/unit/test_docs_sync.py: status is done, in progress or pending, with exactly one in progress."
---

# MVP Scope — v1

**Goal**: record a meeting from a GNOME panel button, get a diarized transcript with nameable speakers,
and generate a summary — all running locally on the developer's machine.

**Definition of v1 Done**:
- F0-F6 deliverables below are done and traced to code and tests.
- A full real-world flow works daily: record, stop, wait for the notification, review and name speakers, generate a summary.
- `make check` is green (ruff, pyrefly, import-linter, pytest).
- No private audio, transcript or meeting name exists anywhere in the repository.

**Non-goals**: live transcription, chat with the meeting, cloud sync/sharing, multi-user, non-GNOME desktops.

---

## IN SCOPE — v1 (F0-F6)

| Phase | Deliverable | User can... | Status |
|---|---|---|---|
| F0 Foundations | Repo scaffold, tooling, import-linter contracts, shared kernel, AGENTS.md, PRD, scope, ADRs 001-007, ARCHITECTURE.md | review decisions before any feature code | done |
| F1 Recording | `recording` domain, D-Bus `Recording1` interface (`Start`/`Stop`, `State`), systemd user service (ADR-001) | record via `gdbus call` | done |
| F2 GNOME extension | Panel button with a drop-down menu to start/stop, timer, status | start/stop from the top-bar menu | done |
| F3 Processing | Queued transcription + diarization on stop, per-speaker voice samples, "Me" auto-label, desktop notification | get "Done: N speakers" | done |
| F4 Review app | Native GTK4 + libadwaita app opened from the extension menu: listen per speaker, rename, merge, view transcript (ADR-005) | do speaker review in a native window | done |
| F5 Summaries | "Generate summary" button, Ollama/OpenCode Go choice, export `.md` | complete the end-to-end flow | in progress |
| F2.5 Install | `make install` / `make uninstall`: service as a `uv` tool, systemd user unit, D-Bus activation, extension enabled; idempotent | install everything with one command, never start the service by hand | done |
| F6 Hardening | Install runbook (update, troubleshoot), resilience checks | use it daily | pending |

**Status**: done, in progress, pending (checked by `tests/unit/test_docs_sync.py`).

---

## OUT OF SCOPE — next release

| Feature | PRD ref | Reason to postpone | Trigger to activate |
|---|---|---|---|
| Live transcription | PRD out of scope | GPU contention on 8 GB, lower quality (ADR-003) | v1 in daily use and a clear need for in-meeting captions (IDEA-001) |
| Chat with the meeting | PRD out of scope | Needs retrieval over transcripts plus a UI | Summaries stable and used daily (IDEA-002) |
| SQLite catalog | ADR-004 | Folder + JSON is enough while listing is cheap | Listing/search latency becomes noticeable (IDEA-003) |

---

## OUT OF SCOPE — indefinitely

| Feature | Why never (or not soon) |
|---|---|
| Cloud sync / sharing | Meetings are private by design; nothing leaves the machine except an explicit LLM call |
| Multi-user / accounts | Single-user tool, per-user D-Bus session bus only |
| Non-GNOME desktops | The recording trigger is a GNOME Shell extension; the D-Bus API could serve other clients later |

---

## Traceability: PRD -> Scope -> Code -> Test

| PRD requirement | Deliverable | Code | Test |
|---|---|---|---|
| NFR local-first: no network listener, D-Bus session bus only  | F0 scaffold | `src/meeting_scribe/main.py`, `src/meeting_scribe/settings.py` | `tests/unit/test_main.py`, `tests/unit/test_settings.py` |
| NFR stable error codes | F0 shared kernel | `src/meeting_scribe/base/domain/errors.py` | `tests/unit/base/test_errors.py` |
| Immutable entities, null-object support | F0 shared kernel | `src/meeting_scribe/base/domain/entity.py` | `tests/unit/base/test_entity.py` |
| Domains independent, layering enforced | F0 import-linter | `pyproject.toml` (`[tool.importlinter]`) | `make imports` |
| Configurable log level | F0 scaffold | `src/meeting_scribe/base/infrastructure/logging.py` | `tests/unit/base/test_logging.py` |
| FR-1 start/stop recording | F1 | `src/meeting_scribe/recording/`, `src/meeting_scribe/main.py` | `tests/unit/recording/`, `tests/e2e/` |
| FR-2 panel button | F2 | `gnome-extension/meeting-scribe@meetingscribe.dev/` | `gnome-extension/tests/` (`make test-extension`), `tests/unit/test_extension_pack.py` |
| FR-3 transcribe + diarize after stop | F3 | `src/meeting_scribe/meetings/`, `src/meeting_scribe/transcription/`, `src/meeting_scribe/speakers/`, `src/meeting_scribe/worker.py` | `tests/unit/meetings/`, `tests/unit/transcription/`, `tests/unit/speakers/` |
| FR-4 voice samples, notification | F3 | `src/meeting_scribe/speakers/` (samples); `src/meeting_scribe/meetings/` (notification, `Meetings1`) | `tests/unit/speakers/` |
| FR-5 review: listen, rename, merge | F4 | `src/meeting_scribe/speakers/`, `src/meeting_scribe/meetings/`, `review-app/src/meeting_scribe_review/`, `gnome-extension/` | `tests/unit/speakers/`, `tests/unit/review_app/`, `tests/e2e/meetings/`, `gnome-extension/tests/` |
| FR-6 summary via LLM provider | F5 | pending | pending |
| FR-7 install | F2.5 (runbook in F6) | `scripts/install.sh`, `scripts/uninstall.sh` | `tests/unit/test_install_scripts.py` |

> When you add a deliverable, fill its row. If a column is empty, that deliverable is not finished.

---

## Changes to this doc

| Date | Change |
|---|---|
| 2026-10-09 | Initial draft from the F0 handoff brief. |
| 2026-10-09 | Phase status brought in line with the code: F0, F1, F2, F2.5 done, F3 in progress; traceability filled for FR-1 to FR-4. |
| 2026-10-09 | F3 done (tracker #52 merged); F4 in progress. |
| 2026-10-11 | F4 done (tracker #82 merged); F5 in progress. |
