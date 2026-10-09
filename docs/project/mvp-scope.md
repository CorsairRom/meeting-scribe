---
type: scope
status: draft
last_updated: 2026-10-09
ai_instructions:
  - "This is the contract of what IS and IS NOT implemented in the current release (v1)."
  - "If a deliverable is not in 'IN SCOPE', do NOT build it now even if it appears in the PRD."
  - "When you complete a deliverable, update its status and add date + commit."
  - "If the PRD and this doc conflict, THIS doc wins for v1."
  - "Work phase by phase: F1 starts only after the F0 review is approved by the user."
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
| F0 Foundations | Repo scaffold, tooling, import-linter contracts, shared kernel, AGENTS.md, PRD, scope, ADRs 001-006, ARCHITECTURE.md | review decisions before any feature code | in progress |
| F1 Recording | `recording` domain, D-Bus `Recording1` interface (`Start`/`Stop`, `State`), systemd user service (ADR-001) | record via `gdbus call` | pending |
| F2 GNOME extension | Panel button with a drop-down menu to start/stop, timer, status | start/stop from the top-bar menu | pending |
| F3 Processing | Queued transcription + diarization on stop, per-speaker voice samples, "Me" auto-label, desktop notification | get "Done: N speakers" | pending |
| F4 Review app | Native GTK4 + libadwaita app opened from the extension menu: listen per speaker, rename, merge, view transcript (ADR-005) | do speaker review in a native window | pending |
| F5 Summaries | "Generate summary" button, Ollama/DeepSeek choice, export `.md` | complete the end-to-end flow | pending |
| F6 Install | `make install` (service + extension), runbook | use it daily | pending |

**Status**: in progress, done, blocked, pending.

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
| NFR local-first: no network listener, D-Bus session bus only (transport arrives in F1) | F0 scaffold | `src/meeting_scribe/main.py`, `src/meeting_scribe/settings.py` | `tests/unit/test_main.py`, `tests/unit/test_settings.py` |
| NFR stable error codes | F0 shared kernel | `src/meeting_scribe/base/domain/errors.py` | `tests/unit/base/test_errors.py` |
| Immutable entities, null-object support | F0 shared kernel | `src/meeting_scribe/base/domain/entity.py` | `tests/unit/base/test_entity.py` |
| Domains independent, layering enforced | F0 import-linter | `pyproject.toml` (`[tool.importlinter]`) | `make imports` |
| Configurable log level | F0 scaffold | `src/meeting_scribe/base/infrastructure/logging.py` | `tests/unit/base/test_logging.py` |
| FR-1 start/stop recording | F1 | pending | pending |
| FR-2 panel button | F2 | pending | pending |
| FR-3 transcribe + diarize after stop | F3 | pending | pending |
| FR-4 voice samples, notification | F3 | pending | pending |
| FR-5 review: listen, rename, merge | F4 | pending | pending |
| FR-6 summary via LLM provider | F5 | pending | pending |
| FR-7 install | F6 | pending | pending |

> When you add a deliverable, fill its row. If a column is empty, that deliverable is not finished.

---

## Changes to this doc

| Date | Change |
|---|---|
| 2026-10-09 | Initial draft from the F0 handoff brief. |
