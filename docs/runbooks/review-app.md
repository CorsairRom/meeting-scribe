---
type: runbook
status: active
last_updated: 2026-10-12
phase: F4
verifiable_by:
  - scripts/install.sh
  - scripts/uninstall.sh
  - scripts/_common.sh
  - packaging/applications/dev.meetingscribe.Review.desktop.in
  - Makefile
  - tests/unit/test_install_scripts.py
ai_instructions:
  - "An agent must NEVER run these steps: the app opens a window, plays audio and talks to a service that holds private meetings. Only the user runs them."
  - "Never open the meetings directory, and never write a speaker name or transcript text into the repo, a log or a doc."
---

# Install, open and troubleshoot the review app

## Purpose

Install the native review app (F4), open it from the panel menu, and fix the usual problems. The app is a thin
D-Bus client of the service (`dev.meetingscribe.Meetings1`): it lists processed meetings, plays one sample per
speaker, renames and merges speakers, and shows the transcript. Design: [`../specs/review-app.md`](../specs/review-app.md).

## Privacy warning

> The app shows real speaker names and transcripts and plays real voice samples through your default audio output.
> Open it only knowingly, with nobody else in earshot if the meeting was private. Nothing it shows belongs in the
> repo, a log or a bug report. An AI agent must not run these steps for you.

## Preconditions

- `make install` finished (see [`gnome-extension.md`](gnome-extension.md) for the extension side).
- System Python (`/usr/bin/python3`) with PyGObject, GTK 4 and libadwaita 1. On Fedora:
  `sudo dnf install python3-gobject gtk4 libadwaita gstreamer1-plugins-good`. The app does not use the `uv` Python.
- At least one meeting in the `DONE` state (the extension shows "Last meeting processed: N speakers").

## Steps

1. Run `make install`. Besides the service and the extension it:
   - copies the app to `<data home>/meeting-scribe/versions/<id>/app/meeting_scribe_review/`;
   - writes the launcher `<data home>/meeting-scribe/versions/<id>/bin/meeting-scribe-review` (system Python,
     `PYTHONPATH` set to that version's `app/`);
   - writes the desktop entry `<data home>/applications/dev.meetingscribe.Review.desktop`, whose `Exec` is the stable
     `<data home>/meeting-scribe/current/bin/meeting-scribe-review` (`<data home>` is `${XDG_DATA_HOME:-~/.local/share}`);
   - the preflight (`./install.sh --check`, rows `gtk.pygobject`, `gtk.gtk4`, `gtk.libadwaita`, `gtk.libsecret`) has
     already checked that system Python can import GTK 4, libadwaita and libsecret: a missing package stops the
     install with zero changes and prints its `dnf` command; the summary says `review app: ready`.
2. Open the app: click the panel indicator, then **Review last meeting**. The menu item is shown whenever the
   service is available and nothing is recording. It opens the newest `DONE` meeting; the app shows an empty state
   when there is none.
3. From a terminal (optional): `~/.local/share/meeting-scribe/current/bin/meeting-scribe-review [--meeting ID]`.
4. Development only: `make run-review-app` starts the app from the working tree with system Python.

## Summary tab

A meeting in the `DONE` state has a **Summary** tab next to **Review** (spec [`../specs/summaries.md`](../specs/summaries.md)).

- One **Generate summary** button uses the provider chosen in the settings (`SUMMARIES_PROVIDER`); its label names
  the provider as the service words it. Nothing is generated until you press it.
- When that provider is the cloud, the label says "cloud" and every press first asks for confirmation: the transcript
  and the speaker names leave this computer. Cancel is the default; only **Send to cloud** sends.
- While it runs the tab shows progress and **Cancel**. A summary older than the speakers or transcript shows an
  "out of date" banner. **Copy** and **Save a copy...** export the text; the app never picks a path itself.
- **Set API key...** (cloud only) stores the key in the GNOME Keyring through libsecret, with the same item
  `secret-tool` makes (`application=meeting-scribe`, `key=opencode-api-key`). The key never goes through the service.
  It needs the `Secret-1` typelib (Fedora: `libsecret`, normally installed with GNOME).

## Speech models (in Preferences)

Preferences has a **Speech models** group: the Hugging Face token field (write-only, stored in the GNOME Keyring as
`application=meeting-scribe`, `key=huggingface-token` through libsecret, never sent to the service over D-Bus), a link
to the pyannote terms page, the status, **Download models** with step-level progress and **Cancel**. The service
downloads; the app only shows the status and sends `Download` and `CancelDownload` (`Models1`). Steps and
troubleshooting: [`preferences.md`](preferences.md).

## Verify

- `gtk-launch dev.meetingscribe.Review` or the menu item opens one window; a second launch focuses it.
- The install summary line reads `review app: ready`.
- A sample plays, a rename survives closing and reopening the app, and a merge can be undone.

## Rollback / troubleshooting

| Symptom | Cause and fix |
|---|---|
| `./install.sh` stops with `[ FIX ] gtk.*` (exit 10) | Run the printed `sudo dnf install ...` command yourself, then `./install.sh --check` again |
| The menu item shows a notification asking you to run `make install` | The desktop entry is missing: run `make install` |
| "The Meeting Scribe service is not running" in the window | `systemctl --user start meeting-scribe.service`, then Retry |
| "The service must be restarted to match this app version" | `systemctl --user restart meeting-scribe.service` (an older service is still running) |
| "Hugging Face refused access", or **Download models** stays disabled | See "Speech models" in [`preferences.md`](preferences.md); a disabled button means a download runs, the token is being stored, or both models are already ready |
| **Set API key...** says the keyring is not reachable | Start gnome-keyring (it runs with the GNOME session); unlock the login keyring if asked |
| The window opens but no sound | Install `gstreamer1-plugins-good`; check the default audio output |
| The app does not start from the menu | Run the launcher from a terminal and read the traceback; never paste meeting text from it |

Uninstall: `make uninstall` removes the desktop entry, the launcher and the app (they live in `versions/`). Meetings,
your `.env` and your `settings.json` are left untouched.

**Preferences**: the same window opens the Preferences dialog from its menu, and `meeting-scribe-review --preferences` (the extension menu item) opens it directly; see [`preferences.md`](preferences.md).
