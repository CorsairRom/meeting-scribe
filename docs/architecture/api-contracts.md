---
type: architecture
status: active
last_updated: 2026-10-09
mvp: true
verifiable_by:
  - src/meeting_scribe/recording/infrastructure/dbus/recording_interface.py
  - src/meeting_scribe/meetings/infrastructure/dbus/meetings_interface.py
  - tests/e2e/recording/test_recording_over_dbus.py
  - tests/e2e/meetings/test_meetings_over_dbus.py
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
| `Stop` | method | `() -> s` | Stop capture cleanly and return the session id. Blocks up to `RECORDING_STOP_TIMEOUT_SECONDS` + 4 s in the worst case (2 s SIGTERM grace + 2 s SIGKILL grace, GO-020). | `NO_ACTIVE_RECORDING` when nothing is recording; `AUDIO_CAPTURE_FAILED` when the capture had crashed or would not stop |
| `State` | property | `s` (read) | `IDLE`, `RECORDING`, `STOPPED` or `FAILED` | — |
| `SessionId` | property | `s` (read) | Current or last session id; empty when `IDLE` | — |
| `ElapsedSeconds` | property | `d` (read) | Seconds since the recording started (frozen once stopped or failed); `0.0` when `IDLE`. Changes continuously and is not announced by `PropertiesChanged` | — |
| `Directory` | property | `s` (read) | Session folder path; empty when `IDLE` | — |
| `FailureReason` | property | `s` (read) | Why the session is `FAILED`; empty otherwise | — |
| `StateChanged` | signal | `(ss)` | `(state, session_id)`, emitted whenever the state changes, including a capture crash detected by the background check (polled about every 2 s) | — |

`org.freedesktop.DBus.Properties.PropertiesChanged` is also emitted with `State`, `SessionId`, `Directory` and
`FailureReason` on every change.

A `Start` after `STOPPED` or `FAILED` begins a new session. If the service shuts down while a recording is active
(SIGINT/SIGTERM) or the bus connection is lost, it stops the capture first. If that stop fails, the failure
is logged at ERROR with its error code. The crash monitor logs a failing poll and keeps polling.

Service exit codes: `0` clean shutdown; `1` bus name already owned; `2` stopping the active recording at
shutdown failed (possible orphaned ffmpeg or truncated final chunk); `3` the D-Bus connection was lost (any
active recording is stopped first).

Example call (documentation only, run it yourself against your own session bus):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Recording1.Start
```

Starting a recording opens the microphone and the system audio monitor: only call it when you intend to record.

### `dev.meetingscribe.Meetings1`

Lists meetings, reports what the processing pipeline is doing and retries a failed job. Same bus name and object
path as `Recording1`. Processing starts by itself when a recording stops; nothing here starts it.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `List` | method | `() -> a(ssdsi)` | One row per meeting that has a readable `meeting.json`, newest first: `(id, started_at, duration_seconds, processing_state, speaker_count)`. `duration_seconds` is the audio length once known, else the recorded time, else `0.0`. A `meeting.json` from a newer schema is listed with state `UNSUPPORTED` | `MEETING_STORAGE_FAILED` |
| `Get` | method | `(s) -> a{sv}` | Details of one meeting. Keys: `id` (s), `started_at` (s), `duration_seconds` (d), `directory` (s), `processing_state` (s), `failure_code` (s), `failure_reason` (s), `speaker_count` (i), `transcript_path` (s, absolute). Absent values are empty strings | `ENTITY_NOT_FOUND_MEETING` for an unknown id; `MEETING_SCHEMA_UNSUPPORTED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `Reprocess` | method | `(s) -> s` | Queue a `DONE` or `FAILED` meeting again and return its new state (`QUEUED`). Caches that are still valid are reused, so a failed diarization does not repeat the transcription | `ENTITY_NOT_FOUND_MEETING`; `MEETING_PROCESSING_ACTIVE` when it is queued or running; `MEETING_STORAGE_FAILED` |
| `ActiveMeetingId` | property | `s` (read) | The meeting being processed; empty when idle | n/a |
| `ActiveState` | property | `s` (read) | `IDLE`, or the active job's state: `TRANSCRIBING`, `DIARIZING` or `EXTRACTING_SAMPLES` | n/a |
| `QueuedCount` | property | `u` (read) | Jobs waiting (not counting the active one) | n/a |
| `ProcessingStateChanged` | signal | `(ss)` | `(meeting_id, state)` on every state change, after the new state is saved in `meeting.json` | n/a |
| `ProcessingFinished` | signal | `(ssi)` | `(meeting_id, state, speaker_count)` when a job ends, with `state` `DONE` or `FAILED` (count `0` when `FAILED`) | n/a |

`PropertiesChanged` is emitted with `ActiveMeetingId`, `ActiveState` and `QueuedCount` when any of them changes.
The processing states are `QUEUED`, `TRANSCRIBING`, `DIARIZING`, `EXTRACTING_SAMPLES`, `DONE`, `FAILED` (and
`UNSUPPORTED` in `List` only). A recording that starts while a job runs stops its worker; the job goes back to the
queue head in the state it had, `ActiveState` returns to `IDLE` and no worker starts until the recording ends.

**Desktop notification.** When a job ends the service calls `org.freedesktop.Notifications.Notify` on the session
bus (app name "Meeting Scribe"): summary `Meeting processed: N speakers` (`no speech detected` for 0, plus `(CPU)`
when it ran on the CPU), or `Meeting processing failed` with the failure message as body. The text never holds
transcript content or speaker names. `PROCESSING_NOTIFY=false` turns it off. A notification service that is missing
or fails is logged at WARNING and never changes the job. Clients must not send a second notification.

Example calls (documentation only, run them against your own session bus):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Meetings1.List
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Meetings1.Reprocess "2026-10-09_101500"
```

---

## Out-of-scope members (documented for visibility)

| Member | Status | Reason |
|---|---|---|
| `dev.meetingscribe.Speakers1`, `Summaries1` | planned (F4+) | The domains exist; only their D-Bus adapters are missing |

---

## How to add a new method, property or signal

1. Update this doc FIRST with the expected request/response.
2. Write the failing test (RED).
3. Define the interface adapter in the domain's `infrastructure/dbus/` (`ServiceInterface` subclass).
4. Implement the member; call the application controller and map its `Result` to a return value or a D-Bus error.
5. Add the new error codes to `error-codes-catalog.md`.
