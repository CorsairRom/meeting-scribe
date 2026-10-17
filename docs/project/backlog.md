---
type: backlog
status: living
last_updated: 2026-10-17
ai_instructions:
  - "This tracks pending and completed work. The per-task checklist lives in odd/tasks/meeting-scribe.md."
  - "Before implementing, check that the matching ADR is 'active' (not 'proposed'). ADRs 001-012 are active."
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

### [EPIC] F7 Polish — summary quality, Preferences polish and CI

The phase after F6 (owner decision of 2026-10-12): the F5 and F6 leftovers, plus CI. Pending: it starts after F6.5
(owner decision of 2026-10-16).

- [ ] Summary quality: duplicate task rows, generic team owners instead of names, topics lost in the reduce step
- [ ] The model list cache is not invalidated when the provider or the key changes
- [ ] Listing threads are abandoned (a listing that times out keeps its thread)
- [ ] The restart wait state survives closing the Preferences dialog
- [ ] A stale `api_key` success arrives after a newer attempt already failed and is shown as current

Follow-ups found in the F6 reviews:

- [ ] The preflight `.env` parsing ignores an `export ` prefix and repeated keys (the last one should win)
- [ ] Orphan `.incomplete` cleanup does not take the Hugging Face per-file lock, so it races a concurrent manual `prefetch-models`
- [ ] Orphans left by other tools in the shared Hugging Face cache block READY until Download models is pressed
- [ ] A keyring write whose callback never arrives keeps the token field busy until the app restarts
- [ ] `CancelDownload` is false during the up-to-30 s fresh cache probe

Added when F6 closed:

- [ ] CI: a GitHub Actions workflow running `make check` (no GPU) as a required status check on `main`
- [ ] Speech models: Download models stays the primary button when the models are Ready (it replies READY without downloading); make it secondary or hide it then

---

## In progress

### [EPIC] F6.5 Desktop tray — the extension's menu outside GNOME Shell

Owner decision of 2026-10-16: a tester runs Fedora KDE, so everything the GNOME extension offers must work without
it, with the least new code. Design: `docs/specs/desktop-tray.md`. Tasks: `odd/tasks/meeting-scribe.md` (F6.5).

- [x] Approved design (`docs/specs/desktop-tray.md`, ADR-012; owner approval 2026-10-16): a StatusNotifierItem tray in standalone GJS that reuses the extension's Shell-free modules
- [x] Tray client with the extension's menu; the icon turns red while recording; the elapsed time in the menu and tooltip (2026-10-17, #150/#152/#153/#155/#156)
- [x] Install, update and uninstall that detect the desktop: the extension on GNOME, the tray elsewhere (#154/#157/#160)
- [x] Runbook and docs (#158)
- [ ] Owner check on Fedora KDE (F6.5.6)

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
| 2026-10-12 | F5 Summaries: `summaries` domain, local (native Ollama) and opt-in cloud (OpenCode Go) providers, `summary.md` export, `Summaries1`, Summary tab in the review app, "Open last summary" in the menu, `Settings1` and the Preferences window with model pickers, cloud API key in the GNOME Keyring (ADR-009) | tracker #99 |
| 2026-10-12 | F6 Install: root `install.sh` with a read-only preflight (`--check`, `--json`, stable exit codes), no `sudo`, prerequisites in the README, Hugging Face token in the keyring and models downloaded from Preferences (`Models1`), idempotent update, uninstall keep-list, install runbook, GPL-2.0-or-later; v1 complete (tag `v1.0.0`) | tracker #130 |
| 2026-10-09 | F3 Processing: queued transcription and diarization after Stop, "Me" label, voice samples, D-Bus `Meetings1`, menu processing state, ML install with model prefetch, real GPU validation | tracker #52 (PRs #50-#80) |
| 2026-10-09 | F2.5 `make install` / `make uninstall`: versioned uv tool, systemd user unit, D-Bus activation, extension enabled, idempotent, no restart while recording | F2.5 |
