---
type: architecture
status: active
last_updated: 2026-10-09
mvp: true
verifiable_by:
  - src/meeting_scribe/recording/infrastructure/dbus/recording_interface.py
  - tests/e2e/recording/test_recording_over_dbus.py
  - "busctl --user introspect dev.meetingscribe.Service /dev/meetingscribe/Service"
ai_instructions:
  - "This doc is the SOURCE OF TRUTH for D-Bus contracts. If a method, property or signal changes, update THIS file in the same commit."
  - "No client or server work starts without this doc updated."
  - "If this doc and the introspected interface drift, this doc wins and the code is fixed."
---

# API Contracts

**Bus**: session bus. **Name**: `dev.meetingscribe.Service`. **Object path**: `/dev/meetingscribe/Service`
**Auth**: none beyond the per-user session bus (any process of the same user can call it).
**Errors**: D-Bus errors named `dev.meetingscribe.Error.<ERROR_CODE>` with a message (see `error-codes-catalog.md`).

Names are proposed and renamable until F6 (CR-011). Constants live in
`src/meeting_scribe/base/infrastructure/dbus/constants.py`.

---

## Conventions

| Topic | Rule |
|---|---|
| Method and signal names | `PascalCase` (D-Bus convention) |
| IDs | string (`s`) |
| Timestamps | ISO 8601 with timezone (`s`) |
| Durations | seconds (`d`) |
| Errors | `dev.meetingscribe.Error.<ERROR_CODE>`; the message is a plain English description, never a stack trace |
| Absent values | empty string, never a missing value |

---

## Interfaces

### `dev.meetingscribe.Recording1`

Controls the single in-progress recording. Nothing records until a client calls `Start`.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `Start` | method | `() -> s` | Resolve the audio devices, create the session folder and start capture. Returns the new session id. Blocks up to about 0.5 s (capture startup check). | `RECORDING_ALREADY_ACTIVE` when a recording is in progress; `AUDIO_DEVICE_NOT_FOUND`; `RECORDING_STORAGE_FAILED`; `AUDIO_CAPTURE_FAILED` |
| `Stop` | method | `() -> s` | Stop capture cleanly and return the session id. Blocks up to 14 s in the worst case. | `NO_ACTIVE_RECORDING` when nothing is recording; `AUDIO_CAPTURE_FAILED` when the capture had crashed or would not stop |
| `State` | property | `s` (read) | `IDLE`, `RECORDING`, `STOPPED` or `FAILED` | — |
| `SessionId` | property | `s` (read) | Current or last session id; empty when `IDLE` | — |
| `ElapsedSeconds` | property | `d` (read) | Seconds since the recording started (frozen once stopped or failed); `0.0` when `IDLE`. Changes continuously and is not announced by `PropertiesChanged` | — |
| `Directory` | property | `s` (read) | Session folder path; empty when `IDLE` | — |
| `FailureReason` | property | `s` (read) | Why the session is `FAILED`; empty otherwise | — |
| `StateChanged` | signal | `(ss)` | `(state, session_id)`, emitted whenever the state changes, including a capture crash detected by the background check (polled about every 2 s) | — |

`org.freedesktop.DBus.Properties.PropertiesChanged` is also emitted with `State`, `SessionId`, `Directory` and
`FailureReason` on every change.

A `Start` after `STOPPED` or `FAILED` begins a new session. If the service shuts down while a recording is active
(SIGINT/SIGTERM), it stops the capture first.

Example call (documentation only, run it yourself against your own session bus):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Recording1.Start
```

Starting a recording opens the microphone and the system audio monitor: only call it when you intend to record.

---

## Out-of-scope members (documented for visibility)

| Member | Status | Reason |
|---|---|---|
| `dev.meetingscribe.Meetings1`, `Speakers1`, `Summaries1` | planned (F3+) | Their domains do not exist yet |

---

## How to add a new method, property or signal

1. Update this doc FIRST with the expected request/response.
2. Write the failing test (RED).
3. Define the interface adapter in the domain's `infrastructure/dbus/` (`ServiceInterface` subclass).
4. Implement the member; call the application controller and map its `Result` to a return value or a D-Bus error.
5. Add the new error codes to `error-codes-catalog.md`.
