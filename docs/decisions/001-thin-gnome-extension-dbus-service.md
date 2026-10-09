---
type: decision
adr_number: 001
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "No ML or business logic inside gnome-shell. The extension only calls the D-Bus API."
  - "No HTTP server. Every client (extension, review app, tests) uses the D-Bus session bus."
---

# ADR-001: Thin GNOME extension and a local Python service over the D-Bus session bus

## Status

`active` — approved by the user in the F0 review (2026-10-09).

**Revision history**: 2026-10-09 — revised during F0 review: HTTP → D-Bus (user decision).

## Context

The product needs a recording trigger inside GNOME Shell (panel button, timer, status) and heavy work
(audio capture, GPU transcription and diarization, LLM calls). GNOME Shell extensions are written in GJS and run
inside the compositor process: a blocking call, a leak or a crash there freezes or takes down the whole desktop
session. GJS also has no ML stack. The review UI is a native GTK4 app (ADR-005) opened from the extension menu,
so no client needs a web transport.

Without a decision, logic would drift into the extension, the part that is hardest to test and most dangerous to break.

## Options Considered

### Option A: ML and logic inside gnome-shell

**Description**: the extension records and processes audio itself, possibly spawning subprocesses.

**Pros**:
- One deployable artifact.
- No IPC.

**Cons**:
- Blocking or crashing code freezes or kills the desktop session.
- GJS has no faster-whisper, pyannote or numpy; everything would be subprocesses anyway.
- Hard to unit-test; every change needs a shell reload (on Wayland, a logout).

**Cost**: low to start, very high in risk and maintenance.

**Rejected.**

### Option B: Localhost HTTP API (FastAPI) under `systemd --user`

**Description**: a Python service bound to `127.0.0.1`; the extension is a GJS client (Soup). Originally proposed in this ADR.

**Pros**:
- Easy to exercise with `curl` and an HTTP test client.
- Service lifecycle independent of the desktop.

**Cons**:
- Any web page open in the user's browser can attempt requests to `127.0.0.1` (CSRF, DNS rebinding), which would need a token, `Host` validation and content-type rules to protect safety rule 1 (never record without explicit user action).
- With no browser UI (ADR-005) there is no reason to speak HTTP at all.
- No native signals: live state (timer, processing finished) needs polling or SSE.

**Cost**: low to start, plus permanent hardening work.

**Rejected in review.**

### Option C: D-Bus session-bus API

**Description**: a Python service (dbus-fast, asyncio) that owns a well-known name on the session bus; the extension and the review app are thin clients.

**Pros**:
- Native to GNOME; first-class in GJS (`Gio.DBusProxy`) and GTK.
- The session bus is per-user and unreachable from websites, so no token, CSRF or `Host` mitigations are needed.
- Signals provide live state (timer, processing finished) without polling.
- Bus activation can start the service on first call.

**Cons**:
- D-Bus type signatures must be specified and maintained.
- Less convenient to poke than `curl`; needs `gdbus` or `busctl`.
- End-to-end tests need a private bus.

**Cost**: moderate; one interface to specify and test.

---

## Decision

**We choose Option C.**

It keeps the compositor safe, is the idiomatic GNOME transport, removes the browser attack surface instead of
mitigating it, and gives signals for live state. Any process of the same user can call the service, which is the
same trust boundary as the user's own files.

---

## Consequences

### Positive

- The extension stays tiny and cannot hang the shell with ML work.
- No network surface: nothing for a website to reach, no hardening scheme to maintain.
- Signals drive the timer and the "processing finished" notification.
- The service can be restarted, upgraded and debugged independently.

### Negative

- Tested with `gdbus call` / `busctl --user` instead of `curl`.
- End-to-end tests need `dbus-run-session` for a private session bus.
- D-Bus type signatures are an extra contract to maintain.
- Two artifacts to install (service and extension), handled in F6.

### Accepted trade-offs

- A less ad-hoc-friendly transport in exchange for native integration and no browser-reachable surface.

### Trigger to revisit

- A non-desktop client (remote or browser) becomes a requirement.
- dbus-fast proves unable to meet the asyncio or typing needs.

---

## Implementation notes

- Library: [dbus-fast](https://github.com/bluetooth-devices/dbus-fast) (asyncio). Interfaces are `ServiceInterface` subclasses using `@dbus_method`, `@dbus_property` and `@dbus_signal`; the service uses `MessageBus().connect()`, `bus.export(path, iface)` and `bus.request_name(name)`. Added as a dependency in F1.
- Proposed names (renamable until F6): bus name `dev.meetingscribe.Service`, object path `/dev/meetingscribe/Service`, interfaces `dev.meetingscribe.Recording1` (Start/Stop, `State` and `ElapsedSeconds` properties, `StateChanged` signal), `dev.meetingscribe.Meetings1` (list/get, `ProcessingFinished` signal), `dev.meetingscribe.Speakers1` (rename/merge/sample path), `dev.meetingscribe.Summaries1`.
- Error mapping: domain errors become D-Bus errors raised as `DBusError("dev.meetingscribe.Error.<ERROR_CODE>", message)`, for example `dev.meetingscribe.Error.ENTITY_NOT_FOUND`. See `docs/architecture/error-codes-catalog.md`.
- Activation: a `systemd --user` unit plus a D-Bus session activation file (`~/.local/share/dbus-1/services/dev.meetingscribe.Service.service` with `SystemdService=`) so the first call starts the service. Installed in F6.
- The service runs one asyncio loop. ML and other blocking work runs off the event loop (worker thread or process) so D-Bus stays responsive.
- Adapters live in each domain's `infrastructure/dbus/*_interface.py`; domain layers must not import `dbus_fast` (enforced by import-linter).
- The extension lives in `gnome-extension/` (F2) and contains no business logic (AP-001).
- Tests: unit tests on interface adapters; e2e under `dbus-run-session`.
- Docs to update when built: `docs/architecture/api-contracts.md`, `docs/project/rules-registry.md`.

---

## References

- `PRD.md` NFR-1, NFR-7 and safety requirements
- [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) section 1
- [`../project/rules-registry.md`](../project/rules-registry.md): AP-001, BR-001
- [ADR-005](005-review-ui-native-gtk-app.md)
- dbus-fast high-level service docs: https://github.com/bluetooth-devices/dbus-fast
