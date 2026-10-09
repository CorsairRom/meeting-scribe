---
type: backlog
status: living
last_updated: 2026-10-09
ai_instructions:
  - "This tracks pending and completed work. The per-task checklist lives in odd/tasks/meeting-scribe.md."
  - "Before implementing, check that the matching ADR is 'active' (not 'proposed'). ADRs 001-006 were approved (active) in the F0 review on 2026-10-09."
  - "When you complete a task: mark it done, add the date, and update mvp-scope.md if it applies."
  - "When you add tasks for an epic: follow the existing format and reference the ADR."
---

# Backlog — meeting-scribe

Pending, in-progress and completed work. One task per line; each epic references an ADR or doc.
Fine-grained task tracking lives in [`odd/tasks/meeting-scribe.md`](../../odd/tasks/meeting-scribe.md).

---

## To do

### [EPIC] F1 Recording — ADR-001, ADR-002, ADR-003

> Unblocked: F0 review approved, ADR-001/002/003 `active` (2026-10-09).

- [ ] `recording` domain: session entity, state transitions as entity methods returning copies
- [ ] ffmpeg capture adapter: 2-channel 30 s chunks (mic + system monitor), stop with `q`, kill fallback, `start_new_session=True`
- [x] D-Bus `dev.meetingscribe.Recording1` (`Start`, `Stop`, `State`, `ElapsedSeconds`, `StateChanged`) via dbus-fast, error mapping to `dev.meetingscribe.Error.<ERROR_CODE>`
- [x] e2e tests on a private session bus (`dbus-daemon`, fakes only)
- [x] systemd `--user` unit template and D-Bus activation file in `packaging/` (installation is F6) (F1.5, 2026-10-09)
- [x] Runbook: record and stop via gdbus (F1.5, 2026-10-09)
- [x] Docs: `architecture/api-contracts.md` (D-Bus interfaces), rules-registry entries
- [x] **Follow-up**: `STORAGE_MEETINGS_DIR=~/...` expanded; `.env` from XDG config; validated `log_level` (F1.1, 2026-10-09)
- [x] ~~**Follow-up**: Starlette `httpx` deprecation warning~~ obsolete: HTTP stack removed (2026-10-09)

### [EPIC] F2 GNOME extension — ADR-001

- [ ] GJS panel indicator: start/stop, timer, status
- [ ] Talk to the service through `Gio.DBusProxy` (call `Recording1`, subscribe to `StateChanged`)
- [ ] Menu item "Review last meeting" launching the review app
- [x] Runbook: try and install the extension (F2.4, 2026-10-09)

### [EPIC] F3 Processing — ADR-002, ADR-003, ADR-004

- [ ] `transcription` domain + faster-whisper adapter (lazy load, release after use, CUDA preload with logging)
- [ ] `speakers` domain + pyannote adapter, voice samples, "Me" labelling by per-turn energy dominance
- [ ] `meetings` domain: catalog, `MeetingRepository` (folder + versioned `meeting.json`), processing queue, single GPU worker
- [ ] Resume pending jobs at service start
- [ ] Desktop notification on completion
- [ ] Cache keyed on processing params (prototype bug)

### [EPIC] F4 Review app — ADR-005

- [ ] GTK4 + libadwaita app on system python, thin D-Bus client; presenters in pure Python (no `gi`) tested with pytest
- [ ] Audio sample playback with `Gtk.MediaFile`
- [ ] Rename and merge speakers
- [ ] Launch from the extension menu "Review last meeting"

### [EPIC] F5 Summaries — ADR-006

- [ ] `summaries` domain: `SummaryProvider` port
- [ ] OpenAI-compatible adapter (Ollama default, DeepSeek opt-in)
- [ ] Cloud opt-in setting, "cloud" label, per-request confirmation
- [ ] Export `.md` in the reference structure

### [EPIC] F6 Install

- [ ] `make install` (service + extension)
- [ ] Runbook: install, update, troubleshoot

---

## In progress

### [EPIC] F0 Foundations

- [x] F0.1 Scaffold + tooling
- [x] F0.2 Shared kernel (errors, `BaseEntity`)
- [x] F0.3 Docs: PRD, ARCHITECTURE, AGENTS, docs taxonomy, templates, project docs (approved 2026-10-09)
- [x] F0.4 ADRs 001-006 (`active`, approved 2026-10-09)
- [x] F0.7 Drop the HTTP scaffold for a transport-neutral composition root (commit `01300e8`, 2026-10-09)
- [x] F0.8 Switch ADR-001/005 to D-Bus and a native GTK4 review app; ripple through docs (2026-10-09)
- [x] F0.5 Remove `HANDOFF.md`, full checks
- [x] F0.6 Present PRD, scope and ADRs to the user for review (approved 2026-10-09)

---

## Completed

| Date | Task | Reference |
|---|---|---|
| 2026-10-09 | F0.1 Scaffold + tooling | commit `8fccc8a` |
| 2026-10-09 | F0.2 Shared kernel | commit `e94370a` |
| 2026-10-09 | F0.7 Transport-neutral composition root | commit `01300e8` |
| 2026-10-09 | F0.8 ADR-001/005 revised: D-Bus + native GTK4 review app | commit `b8e6b97` |
