---
type: backlog
status: living
last_updated: 2026-10-09
ai_instructions:
  - "This tracks pending and completed work. The per-task checklist lives in odd/tasks/meeting-scribe.md."
  - "Before implementing, check that the matching ADR is 'active' (not 'proposed'). ADRs 001-008 are active."
  - "When you complete a task: mark it done, add the date, and update mvp-scope.md if it applies."
  - "When you add tasks for an epic: follow the existing format and reference the ADR."
---

# Backlog — meeting-scribe

Pending, in-progress and completed work. One task per line; each epic references an ADR or doc.
Fine-grained task tracking lives in [`odd/tasks/meeting-scribe.md`](../../odd/tasks/meeting-scribe.md).

---

## To do

### [EPIC] Later improvements (not scheduled)

Raised after the first real recording (2026-10-09); recorded so nothing is lost, not planned into a phase yet.

- [ ] Storage layout: the meeting folder in `~/Documents/meetings` keeps only what the user reads (transcript, summary, optionally audio); `meeting.json` and `samples/` move to the XDG data dir, `words.json` / `turns.json` / `utterances.json` to the XDG cache dir, `processing.log` / `ffmpeg.log` to the service journal. Touches the worker job contract (one `meeting_dir` today), not only the repository adapter
- [ ] Readable meeting folder names (date and time, title from the F5 summary) while keeping a stable internal id
- [ ] Audio retention: setting to keep or drop the audio after review or after N days; optional Opus compression (about 230 MB/h today, about 20 MB/h as Opus)
- [ ] A database (SQLite) only if search or long meeting lists need it; behind the `MeetingRepository` port
- [ ] Recognise the same speaker across meetings (voice embeddings), out of the MVP
- [ ] Suggest speaker names already used in earlier meetings while naming (F4 OQ-5: typed by hand for now); design together with cross-meeting voice recognition
- [ ] Open the review app from the "Meeting processed" notification (F4 OQ-6: menu only for now); a `desktop-entry` hint needs a manual check in GNOME Shell
- [ ] Import recordings made before F3 (no `meeting.json`) so they can be processed

### [EPIC] F6 Hardening

- [ ] Runbook: install, update, troubleshoot

---

## In progress

### [EPIC] F5 Summaries — ADR-006

- [ ] `summaries` domain: `SummaryProvider` port
- [ ] OpenAI-compatible adapter (Ollama default, OpenCode Go opt-in)
- [ ] Cloud opt-in setting, "cloud" label, per-request confirmation
- [ ] Export `.md` in the reference structure
- [ ] Extension menu action that opens the last meeting's summary (decided 2026-10-09: the menu opens the summary, not the transcript, so it waits for F5); show the meeting date and time in a readable form instead of the folder id
---

## Completed

| Date | Task | Reference |
|---|---|---|
| 2026-10-09 | F0.1 Scaffold + tooling | commit `8fccc8a` |
| 2026-10-09 | F0.2 Shared kernel | commit `e94370a` |
| 2026-10-09 | F0.7 Transport-neutral composition root | commit `01300e8` |
| 2026-10-09 | F0.8 ADR-001/005 revised: D-Bus + native GTK4 review app | commit `b8e6b97` |
| 2026-10-09 | F0.3-F0.6 Docs, ADRs 001-006 and the F0 review approved | F0 |
| 2026-10-09 | F1 Recording: `recording` domain, ffmpeg capture adapter, D-Bus `Recording1`, e2e on a private bus, systemd unit and activation file, runbook | PRs #16-#21 |
| 2026-10-09 | F2 GNOME extension: panel indicator, D-Bus client, controller, packaging, runbook | PRs #33-#48 |
| 2026-10-11 | F4 Review app: GTK4 + libadwaita app from the menu (listen per speaker, rename, merge, unmerge, transcript), Meetings1 review members, non-destructive review in meeting.json, install with the service | tracker #82 (PRs #81-#97) |
| 2026-10-09 | F3 Processing: queued transcription and diarization after Stop, "Me" label, voice samples, D-Bus `Meetings1`, menu processing state, ML install with model prefetch, real GPU validation | tracker #52 (PRs #50-#80) |
| 2026-10-09 | F2.5 `make install` / `make uninstall`: versioned uv tool, systemd user unit, D-Bus activation, extension enabled, idempotent, no restart while recording | F2.5 |
