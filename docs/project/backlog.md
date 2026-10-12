---
type: backlog
status: living
last_updated: 2026-10-12
ai_instructions:
  - "This tracks pending and completed work. The per-task checklist lives in odd/tasks/meeting-scribe.md."
  - "Before implementing, check that the matching ADR is 'active' (not 'proposed'). ADRs 001-009 are active."
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

### [EPIC] Summary quality and Preferences polish (after F6)

A separate phase after F6 (owner decision of 2026-10-12); the F5 leftovers, not part of F6.

- [ ] Summary quality: duplicate task rows, generic team owners instead of names, topics lost in the reduce step
- [ ] The model list cache is not invalidated when the provider or the key changes
- [ ] Listing threads are abandoned (a listing that times out keeps its thread)
- [ ] The restart wait state survives closing the Preferences dialog
- [ ] A stale `api_key` success arrives after a newer attempt already failed and is shown as current

---

## In progress

### [EPIC] F6 Install — spec `docs/specs/install.md` (written next, for owner approval)

Owner decisions of 2026-10-12 (binding):

- [ ] Scope: a robust install from the repo. Distribution through extensions.gnome.org is deferred to a later phase. There is no cloud-only install mode: transcription and diarization need the local NVIDIA GPU, so the cloud only replaces the summary step
- [ ] One entry point: root-level `install.sh` (`make install` stays as an alias); uninstall mirrors it
- [ ] Preflight first: checks every prerequisite before changing anything; when something is missing it stops with zero changes and prints what is missing, why it is needed and the exact fix command. `./install.sh --check` runs only the preflight. Stable exit codes; output readable by humans and parseable by AI agents
- [ ] The installer never runs `sudo` and never prompts mid-way: it prints the commands. Human-only steps (NVIDIA driver and reboot, accepting the pyannote terms and creating the Hugging Face token) are labelled apart from fixable errors
- [ ] Prerequisites listed in the README and checked by the preflight: NVIDIA GPU with a working driver, at least 8 GB VRAM, free disk space, GNOME on Wayland, `uv`, GTK4, libadwaita and PyGObject, Ollama installed and reachable (the install stops if it is missing)
- [ ] The Hugging Face token moves to Preferences (GNOME Keyring, like the cloud API key) with a "Download models" action; processing loads the cached models with no token. The installer does not stop for a missing token: it reports "models pending"
- [ ] Update is `git pull` then `./install.sh` (idempotent; keeps meetings, `settings.json`, keyring entries and models); uninstall keeps user data and says so
- [ ] Runbook `docs/runbooks/install.md` (install, update, troubleshoot) and a README section "Install (humans and AI agents)"

The task breakdown (F6.1 and on) lives in `odd/tasks/meeting-scribe.md`.

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
| 2026-10-09 | F3 Processing: queued transcription and diarization after Stop, "Me" label, voice samples, D-Bus `Meetings1`, menu processing state, ML install with model prefetch, real GPU validation | tracker #52 (PRs #50-#80) |
| 2026-10-09 | F2.5 `make install` / `make uninstall`: versioned uv tool, systemd user unit, D-Bus activation, extension enabled, idempotent, no restart while recording | F2.5 |
