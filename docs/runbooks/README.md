---
type: index
status: living
last_updated: 2026-10-17
---

# Runbooks

**Question answered here**: how do I run this operation step by step?

A runbook gives exact commands, preconditions and what to verify afterwards.

- **Naming**: `verb-object.md` in kebab-case (for example `install-service.md`, `recover-failed-job.md`).
- **Structure**: Purpose, Preconditions, Steps (numbered, copy-pasteable), Verify, Rollback / troubleshooting.
- **Safety**: a runbook never starts a recording or plays audio on its own; steps that do say so and require the user to act.
- **Not here**: the reasoning behind a choice (`decisions/`).

| Runbook | Phase | Status |
|---|---|---|
| [Record and stop a meeting via gdbus](record-via-gdbus.md) | F1 | active |
| [Try and install the GNOME extension](gnome-extension.md) | F2 | active |
| [Set up and troubleshoot processing](processing.md) | F3 | active |
| [Install, open and troubleshoot the review app](review-app.md) | F4 | active |
| [Set up and troubleshoot summaries](summaries.md) | F5 | active |
| [Change settings in Preferences](preferences.md) | F5 | active |
| [Store the cloud API key in the GNOME Keyring](store-cloud-api-key.md) | F5 | active |
| [Install, update, uninstall and troubleshoot](install.md) | F6 | active |
| [Run and troubleshoot the desktop tray](desktop-tray.md) | F6.5 | active |
