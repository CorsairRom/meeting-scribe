---
type: backlog
status: living
last_updated: 2026-10-09
ai_instructions:
  - "This tracks pending and completed work. The per-task checklist lives in odd/tasks/meeting-scribe.md."
  - "Before implementing, check that the matching ADR is 'active' (not 'proposed'). ADRs 001-007 are active."
  - "When you complete a task: mark it done, add the date, and update mvp-scope.md if it applies."
  - "When you add tasks for an epic: follow the existing format and reference the ADR."
---

# Backlog — meeting-scribe

Pending, in-progress and completed work. One task per line; each epic references an ADR or doc.
Fine-grained task tracking lives in [`odd/tasks/meeting-scribe.md`](../../odd/tasks/meeting-scribe.md).

---

## To do

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

### [EPIC] F6 Hardening

- [ ] Runbook: install, update, troubleshoot

---

## In progress

### [EPIC] F3 Processing — ADR-002, ADR-003, ADR-004, ADR-007; spec [`processing.md`](../specs/processing.md)

- [x] F3.0 Design: processing spec + ADR-007 (GPU work in short-lived worker processes); open product questions answered by the user
- [x] F3.1 `meetings` domain: `Meeting` entity and processing state machine, `MeetingRepository` (folder + versioned `meeting.json`), FIFO queue, worker protocol and subprocess runner (timeouts, termination), resume at service start
- [x] F3.2 `transcription` domain + faster-whisper worker stage (lazy import, CUDA preload with logging, no silent CPU fallback, params-keyed `words.json`), ML-isolation import contract
- [x] F3.3a `speakers` domain: word assignment, utterances, numbering, transcript rendering, "Me" by per-frame energy dominance with bleed compensation, voice samples (PRs #65, #66)
- [x] F3.3b pyannote diarization worker stage on the system channel
- [x] F3.4a Wiring: Stop enqueues processing, recording preempts GPU work; D-Bus `Meetings1` (`List`, `Get`, `Reprocess`, processing signals); desktop notification
- [x] F3.4b Extension shows the processing state (`Meetings1` proxy, menu line)
- [ ] F3.5 `make install` with the ML extra and model prefetch, runbook [`processing.md`](../runbooks/processing.md) (done); real-recording validation with consent (pending, run by the user)

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
| 2026-10-09 | F2.5 `make install` / `make uninstall`: versioned uv tool, systemd user unit, D-Bus activation, extension enabled, idempotent, no restart while recording | F2.5 |
