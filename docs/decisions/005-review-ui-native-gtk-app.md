---
type: decision
adr_number: 005
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "The review app is a thin D-Bus client: no business logic, no ML, no direct file access to meeting data."
  - "Presenters and view-models must not import gi; only the thin views may."
---

# ADR-005: Review UI as a native GTK4 + libadwaita app

## Status

`active` — approved by the user in the F0 review (2026-10-09).

**Revision history**: 2026-10-09 — revised during F0 review: HTTP → D-Bus (user decision).

## Context

After processing, the user needs a UI to listen to a voice sample per speaker, rename speakers, merge wrongly
split ones, read the transcript and trigger a summary. It is a single-user desktop tool with a handful of screens.
The user expects everything to happen from GNOME: the extension menu offers "Review last meeting", which opens
the review UI. The UI talks to the service over D-Bus (ADR-001).

## Options Considered

### Option A: Browser page with Jinja2 and htmx

**Description**: server-rendered HTML served by the service. Originally proposed in this ADR.

**Pros**:
- No build step; the browser plays audio for free.

**Cons**:
- The user wants everything inside GNOME, not in a browser tab.
- It would force an HTTP server back into the service, which ADR-001 rejects.

**Rejected in review.**

### Option B: Everything inside the shell panel menu

**Description**: transcript, speaker rename and merge inside the extension's popup menu.

**Pros**:
- No second process or app.

**Cons**:
- Too cramped for a transcript and merge flow.
- Complex UI inside gnome-shell is risky for the session (ADR-001).

**Rejected.**

### Option C: GJS + GTK4 app

**Description**: a GTK4/libadwaita app written in GJS, the same language as the extension.

**Pros**:
- One language across extension and app; GJS runs on the system without extra setup.

**Cons**:
- No pytest; a second test toolchain to maintain.
- Presenter logic could not share Python tooling and lint rules with the rest of the repo.

**Cost**: moderate; viable fallback.

### Option D: Python + PyGObject GTK4 app

**Description**: a GTK4/libadwaita app in Python, run as a separate process.

**Pros**:
- Same language, linters and test runner as the service.
- Presenters unit-testable with pytest.
- Native look and feel; audio playback through GStreamer.

**Cons**:
- Runs on system Python, not the project venv (see Implementation notes).
- A second UI stack alongside the extension's GJS.

**Cost**: moderate.

---

## Decision

**We choose Option D.**

A native GTK4 + libadwaita app, a separate process and a thin D-Bus client, opened from the extension menu. It keeps
the UI inside GNOME, keeps risky UI code out of the compositor, and stays testable with the project's Python tooling.

---

## Consequences

### Positive

- Native GNOME look and feel with no browser involved.
- No HTTP surface and no web hardening.
- Presenter logic is unit-tested with pytest; views stay thin.

### Negative

- Two Python environments: the service in the venv, the app on system Python.
- A GTK UI is harder to test end to end than HTML; no UI automation in v1.
- A second UI stack (GTK) next to the extension's GJS.

### Accepted trade-offs

- A split runtime (system Python for the app) in exchange for no compilation and no ML dependencies in the UI.

### Trigger to revisit

- The system-Python versus venv split causes friction: install `gobject-introspection-devel` and `cairo-gobject-devel` and build PyGObject into the venv, or move to GJS (Option C).
- A feature needs UI that GTK4/libadwaita cannot express cleanly (for example a waveform editor).

---

## Implementation notes

- **Runtime**: run with the system `/usr/bin/python3` and system PyGObject. The project venv is Python 3.12 (required by the ML stack), where the system `gi` is not importable; the app needs no ML dependencies and no compilation. Fedora 44 ships python3-gobject 3.56.3, gtk4 4.22.4 and libadwaita 1.9.3.
- **Structure** (container-presentational): presenters and view-models are pure Python with no `gi` imports and are unit-tested with pytest in the project venv; GTK views are thin.
- **D-Bus client**: the app calls the interfaces defined in ADR-001 (`Meetings1`, `Speakers1`, `Summaries1`); the "cloud" summary flow keeps its per-request confirmation (ADR-006), shown as a dialog.
- **Audio samples**: played with `Gtk.MediaFile` (GStreamer; needs `gstreamer1-plugins-good`).
- **Launch**: the extension menu item "Review last meeting" starts the app.
- **F4 implementation note**: F4 uses `Meetings1` only (GetSpeakers, GetTranscript, GetSpeakerSample, RenameSpeaker, MergeSpeakers, UnmergeSpeaker, SpeakersChanged). `Speakers1` and `Summaries1` are not built.
- Docs to update when built (done): `docs/architecture/api-contracts.md`, `docs/specs/` for rename/merge behavior.

---

## References

- `PRD.md` FR-5
- [ADR-001](001-thin-gnome-extension-dbus-service.md), [ADR-006](006-summary-provider-openai-compatible.md)
- [`../project/rules-registry.md`](../project/rules-registry.md): BR-003
