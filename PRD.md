---
type: prd
status: approved
last_updated: 2026-10-12
ai_instructions:
  - "This PRD describes the full product vision. What is built NOW is defined by docs/project/mvp-scope.md; if they conflict, mvp-scope.md wins."
  - "Safety requirements (section 6) are non-negotiable and override any convenience."
  - "Never put real meeting content, names or topics in this repo. Use placeholders."
---

# PRD: meeting-scribe

> **A local, GPU-accelerated meeting assistant for GNOME: record, transcribe, tell speakers apart, summarize. Nothing leaves the PC unless you say so.**

**Version**: 0.1.0-draft
**Author**: Richard
**Date**: 2026-10-09
**Status**: Approved (F0 review, 2026-10-09)

> Scope for the current release: [`docs/project/mvp-scope.md`](docs/project/mvp-scope.md).
> Rules and gotchas: [`docs/project/rules-registry.md`](docs/project/rules-registry.md).
> System design: [`ARCHITECTURE.md`](ARCHITECTURE.md). Decisions: [`docs/decisions/`](docs/decisions/).

---

## 1. Problem

Remote meetings produce decisions and action items that are lost unless someone takes notes.
Existing options fall short for this user:

- No GNOME Shell extension offers record, transcribe, diarize and summarize (checked on extensions.gnome.org).
- Meetily is the closest app, but its Community Edition has no speaker diarization (paid tier only, "coming soon") and on Linux it must be compiled.
- Cloud transcription services send private meeting audio off the machine.

A working CLI prototype (two scripts) proved the ML pipeline: a 55-minute meeting was transcribed and
split into speakers in about 3 minutes on the local GPU. It has no service, UI, review flow or summary feature.

## 2. Goal

Turn the prototype into a daily-use tool: a GNOME panel menu to start and stop a recording,
automatic transcription and diarization afterwards, a native GTK4 review app, opened from the GNOME extension menu, to name and merge speakers, and an
optional LLM summary with a fixed reference structure. Everything runs locally; the only thing that may
leave the machine is the transcript sent to an explicitly configured LLM provider.

## 3. Users

A **single user**: the developer, on Fedora 44, GNOME Shell 50 (Wayland), with an NVIDIA RTX 5060 Laptop
GPU (8 GB). No accounts, no multi-user, no remote access.

## 4. User stories (by phase)

| Phase | Story |
|---|---|
| F1 Recording | As the user, I want to start and stop a recording through a local D-Bus API, so that the capture logic is proven before any UI exists. |
| F1 Recording | As the user, I want audio saved in chunks while recording, so that a crash never loses the meeting. |
| F2 GNOME extension | As the user, I want a panel button with a timer and status, so that I can start and stop from its drop-down menu without accidental recordings. |
| F3 Processing | As the user, I want the meeting transcribed and diarized automatically after I stop, so that I do no manual steps. |
| F3 Processing | As the user, I want a desktop notification like "Done: N speakers", so that I know when to review. |
| F3 Processing | As the user, I want my own voice labelled "Me" automatically, so that I do not have to name myself every time. |
| F4 Review app | As the user, I want to listen to a voice sample per speaker, so that I can tell who is who. |
| F4 Review app | As the user, I want to name speakers and merge wrongly split ones, so that the transcript is correct. |
| F4 Review app | As the user, I want to read the full transcript with speaker names and timestamps. |
| F5 Summaries | As the user, I want a "Generate summary" button producing topics, decisions, action items and open questions, so that I do not write minutes. |
| F5 Summaries | As the user, I want to choose between a local model and a cloud model, with the cloud one clearly marked, so that I control what leaves my machine. |
| F5 Summaries | As the user, I want to export the summary as Markdown. |
| F2.5 Install | As the user, I want a single `make install` for the service and the extension, so that daily use survives reboots. |

## 5. Requirements

### 5.1 Functional

| ID | Requirement | Phase |
|---|---|---|
| FR-1 | Start, stop and query status of a recording through the D-Bus session-bus API | F1 |
| FR-2 | GNOME panel button with start/stop, timer and status | F2 |
| FR-3 | On stop, enqueue a job that transcribes (word timestamps) and diarizes, one GPU job at a time | F3 |
| FR-4 | Extract a voice sample per speaker; send a desktop notification on completion | F3 |
| FR-5 | Review app (GTK4, opened from the extension menu): listen per speaker, rename, merge speakers, view transcript | F4 |
| FR-6 | Summary through a `SummaryProvider` (Ollama local default, OpenCode Go cloud opt-in), export `.md` | F5 |
| FR-7 | Installable as a `systemd --user` service plus GNOME extension | F2.5, F6 |

### 5.2 Non-functional

| ID | Requirement |
|---|---|
| NFR-1 | **Local-first**: the service exposes only a D-Bus session-bus API, with no network listener; no telemetry, no background uploads. |
| NFR-2 | **Privacy**: meeting data lives only under the configured meetings directory (default `~/Documents/meetings/`), never in the repo or logs. |
| NFR-3 | **GPU budget**: fits an 8 GB GPU. Models load lazily, are released between stages, and only one GPU job runs at a time. |
| NFR-4 | **Performance reference**: about 3 minutes of processing for a 55-minute meeting on the reference GPU (measured on the prototype). |
| NFR-5 | **Durability**: audio is persisted in 30 s chunks during recording; pending jobs resume after a service restart. |
| NFR-6 | **Stable errors**: every failure carries a stable UPPER_SNAKE `error_code` ([catalog](docs/architecture/error-codes-catalog.md)). |
| NFR-7 | **No web-reachable surface**: the API lives on the per-user D-Bus session bus, so websites cannot reach it; any process of the same user can (same trust boundary as the user's files) (ADR-001). |
| NFR-8 | **Maintainability**: independent domains enforced by import-linter; strict TDD; `make check` green. |

## 6. Safety requirements (non-negotiable)

These come from the project history: a test once played speech into the user's headset and recorded a live meeting.

1. **Never play audio or start a recording without explicit user action.** Tests use synthetic WAV fixtures and never touch audio devices.
2. **Meeting audio and transcripts are private.** They stay under the configured meetings directory, never in the repo, test fixtures, docs or logs, and are never sent anywhere except an explicitly configured LLM provider.
3. **The cloud LLM provider is opt-in** and visibly labelled "cloud" in the review app.

## 7. Out of scope (v1)

- Live transcription (IDEA-001)
- Chat with the meeting (IDEA-002)
- Cloud sync or sharing
- Multi-user, accounts, remote access
- Non-GNOME desktops

## 8. Success metrics

| Metric | Target |
|---|---|
| End-to-end flow (record, stop, notification, review, summary) used for real meetings | daily, without manual shell work |
| Processing time for a 55-minute meeting | about 3 minutes or better |
| Speaker review effort | at most a few clicks per speaker (name or merge) |
| Audio lost on service crash | at most the last unsaved chunk (30 s) |
| Private data found in the repository | none |
| `make check` | green on every commit |

## 9. Open questions

1. Diarization of the system channel only (ADR-002): is per-turn energy dominance enough to decide "Me" with loudspeakers? Answered: the algorithm is specified in [`processing.md`](docs/specs/processing.md) section 2.4 and validated against a real recording at the end of F3.
2. Summary language: follow the meeting language detected by Whisper, or a setting? Answered: it is auto-detected (`SUMMARIES_LANGUAGE=auto|ISO`, `auto` by default) and the headings follow the meeting language; see [`summaries.md`](docs/specs/summaries.md) and [`summaries` runbook](docs/runbooks/summaries.md).
3. Retention: should old meetings' raw audio be prunable after a summary exists?
4. Should the review app also be launchable standalone (desktop entry), not only from the extension menu (ADR-005)?
5. Which Ollama model is the default (size versus GPU memory while Whisper is released)? Answered: `qwen3:4b-instruct-2507-q4_K_M` at a 16384-token context, chosen after measuring on an 8 GB GPU; see [`summaries.md`](docs/specs/summaries.md) (OQ-6) and the [runbook](docs/runbooks/summaries.md).
