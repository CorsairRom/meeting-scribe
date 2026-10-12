---
type: architecture
status: active
last_updated: 2026-10-12
mvp: true
verifiable_by:
  - src/meeting_scribe/recording/infrastructure/dbus/recording_interface.py
  - src/meeting_scribe/meetings/infrastructure/dbus/meetings_interface.py
  - tests/e2e/recording/test_recording_over_dbus.py
  - tests/e2e/meetings/test_meetings_over_dbus.py
  - src/meeting_scribe/meetings/infrastructure/dbus/summaries_interface.py
  - tests/e2e/meetings/test_summaries_over_dbus.py
  - src/meeting_scribe/preferences/infrastructure/dbus/settings_interface.py
  - tests/e2e/preferences/test_settings_over_dbus.py
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

Lists meetings, reports what the processing pipeline is doing, retries a failed job and serves the speaker review
of a finished meeting (speakers, transcript, voice samples, rename, merge, unmerge). Same bus name and object path
as `Recording1`. Processing starts by itself when a recording stops; nothing here starts it.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `List` | method | `() -> a(ssdsi)` | One row per meeting that has a readable `meeting.json`, newest first: `(id, started_at, duration_seconds, processing_state, speaker_count)` (`speaker_count` counts active speakers, merged-away ones excluded). `duration_seconds` is the audio length once known, else the recorded time, else `0.0`. A `meeting.json` from a newer schema is listed with state `UNSUPPORTED` | `MEETING_STORAGE_FAILED` |
| `Get` | method | `(s) -> a{sv}` | Details of one meeting. Keys: `id` (s), `started_at` (s), `duration_seconds` (d), `directory` (s), `processing_state` (s), `failure_code` (s), `failure_reason` (s), `speaker_count` (i, active speakers: merged-away ones are not counted), `transcript_path` (s, absolute). Absent values are empty strings | `ENTITY_NOT_FOUND_MEETING` for an unknown id; `MEETING_SCHEMA_UNSUPPORTED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `Reprocess` | method | `(s) -> s` | Queue a `DONE` or `FAILED` meeting again and return its new state (`QUEUED`). Caches that are still valid are reused, so a failed diarization does not repeat the transcription | `ENTITY_NOT_FOUND_MEETING`; `MEETING_PROCESSING_ACTIVE` when it is queued or running; `MEETING_STORAGE_FAILED` |
| `GetSpeakers` | method | `(s) -> aa{sv}` | `(meeting_id)`: one row per speaker, active speakers first ("Me", then by talk time), merged-away ones after. Keys: `id` (s), `label` (s), `name` (s, empty when unset), `display` (s, the name or else the label), `is_me` (b), `talk_seconds` (d, includes merged speakers), `utterance_count` (i, same folding), `has_sample` (b), `merged_into` (s, target id or empty) | `ENTITY_NOT_FOUND_MEETING`; `MEETING_NOT_PROCESSED` unless the state is `DONE`; `MEETING_SCHEMA_UNSUPPORTED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `GetTranscript` | method | `(s) -> a(sdds)` | `(meeting_id)`: `(speaker_id, start_seconds, end_seconds, text)` in time order, with merges applied (`speaker_id` is the effective id); plain text, no markdown; empty list for a meeting without speech. Repairs a stale `transcript.md` first. Clients never read files | same as `GetSpeakers`, plus `ENTITY_NOT_FOUND_TRANSCRIPT` |
| `GetSpeakerSample` | method | `(ss) -> ay` | `(meeting_id, speaker_id)`: the WAV file of the speaker's voice sample as bytes (mono, 16 kHz, 16-bit, at most about 0.5 MB). No path is returned; the speaker id is checked against `meeting.json` before any file is opened | `ENTITY_NOT_FOUND_MEETING`; `ENTITY_NOT_FOUND_SPEAKER`; `ENTITY_NOT_FOUND_SPEAKER_SAMPLE` (none listed, or the file is gone or unreadable); `MEETING_NOT_PROCESSED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `RenameSpeaker` | method | `(sss) -> aa{sv}` | `(meeting_id, speaker_id, name)`: sets the name (trimmed; an empty name clears it) and returns the refreshed `GetSpeakers` rows. `meeting.json` and `transcript.md` are saved before the call returns | `INVALID_VALUE` (over 64 characters or a control character); `SPEAKER_NAME_TAKEN`; `SPEAKER_MERGE_INVALID` (merged-away speaker); `ENTITY_NOT_FOUND_SPEAKER`; `MEETING_NOT_PROCESSED`; the lookup errors; `MEETING_STORAGE_FAILED` |
| `MergeSpeakers` | method | `(sss) -> aa{sv}` | `(meeting_id, source_id, target_id)`: shows the source as the target everywhere; non-destructive. Returns the refreshed rows | `SPEAKER_MERGE_INVALID`; `ENTITY_NOT_FOUND_SPEAKER`; `MEETING_NOT_PROCESSED`; the lookup errors; `MEETING_STORAGE_FAILED` |
| `UnmergeSpeaker` | method | `(ss) -> aa{sv}` | `(meeting_id, speaker_id)`: undoes a merge and returns the refreshed rows; a success with no change when the speaker is not merged | `SPEAKER_NAME_TAKEN` (the source's own name now clashes); `ENTITY_NOT_FOUND_SPEAKER`; `MEETING_NOT_PROCESSED`; the lookup errors; `MEETING_STORAGE_FAILED` |
| `ActiveMeetingId` | property | `s` (read) | The meeting being processed; empty when idle | n/a |
| `ActiveState` | property | `s` (read) | `IDLE`, or the active job's state: `TRANSCRIBING`, `DIARIZING` or `EXTRACTING_SAMPLES` | n/a |
| `QueuedCount` | property | `u` (read) | Jobs waiting (not counting the active one) | n/a |
| `ProcessingStateChanged` | signal | `(ss)` | `(meeting_id, state)` on every state change, after the new state is saved in `meeting.json` | n/a |
| `ProcessingFinished` | signal | `(ssi)` | `(meeting_id, state, speaker_count)` when a job ends, with `state` `DONE` or `FAILED` (count `0` when `FAILED`); the count is what the run detected, before any merge | n/a |
| `SpeakersChanged` | signal | `(s)` | `(meeting_id)` after a rename, merge or unmerge that changed something, and when a job ends `DONE` (a reprocess clears names and merges); clients refresh with `GetSpeakers`. Not emitted for a no-op | n/a |

`PropertiesChanged` is emitted with `ActiveMeetingId`, `ActiveState` and `QueuedCount` when any of them changes.
The processing states are `QUEUED`, `TRANSCRIBING`, `DIARIZING`, `EXTRACTING_SAMPLES`, `DONE`, `FAILED` (and
`UNSUPPORTED` in `List` only). A recording that starts while a job runs stops its worker; the job goes back to the
queue head in the state it had, `ActiveState` returns to `IDLE` and no worker starts until the recording ends.

**Speaker review.** The review members work only on a `DONE` meeting (`MEETING_NOT_PROCESSED` otherwise) and run
under the same lock as the processing loop, so an edit never overwrites a state change. Edits are last-write-wins
per call: each call returns the fresh rows and emits `SpeakersChanged`, so every client refreshes. Error messages
carry ids, never a speaker name or transcript text. Names and merges are cleared when the meeting is reprocessed.
The review app (F4) is the client; it never opens a meeting file.

**Desktop notification.** When a job ends the service calls `org.freedesktop.Notifications.Notify` on the session
bus (app name "Meeting Scribe"): summary `Meeting processed: N speakers` (`no speech detected` for 0, plus `(CPU)`
when it ran on the CPU), or `Meeting processing failed` with the failure message as body. The text never holds
transcript content or speaker names. `PROCESSING_NOTIFY=false` turns it off. A notification service that is missing
or fails is logged at WARNING and never changes the job. Clients must not send a second notification.

**Extension client (F3.4b).** `gnome-extension/.../lib/meetingsClient.js` watches the bus name without autostart,
reads `ActiveMeetingId`, `ActiveState` and `QueuedCount` (and their `PropertiesChanged`), keeps the last
`ProcessingFinished` payload and calls `Reprocess` with a 10 s timeout. It does not call `List` or `Get` and never
reads meeting content; the menu shows the stage, the queue length, the speaker count, and a Reprocess action after
`FAILED`. Signals missed before it subscribed are not replayed: it starts from the properties.

Example calls (documentation only, run them against your own session bus):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Meetings1.List
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Meetings1.Reprocess "2026-10-09_101500"
```

---

### `dev.meetingscribe.Summaries1`

Summarizes a finished meeting with the configured provider (local Ollama, or the opt-in cloud gateway), one summary at
a time. Same bus name and object path as `Recording1`. A summary never starts by itself: only `Summarize` starts one.
Spec: [summaries](../specs/summaries.md) section 10.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `Providers` | method | `() -> aa{sv}` | One row per configured provider: `id` (s, `local` or `cloud`), `label` (s, `Local (<model>)` or `cloud: <name>, <model>`; the cloud label always contains the word "cloud"), `cloud` (b), `model` (s), `available` (b), `reason` (s, empty or an error code such as `SUMMARY_CLOUD_DISABLED`), `default` (b, the row chosen by `SUMMARIES_PROVIDER`). Local availability is not probed and the key is not read. The cloud URL and the key are never returned | none |
| `Summarize` | method | `(ssb) -> s` | `(meeting_id, provider_id, cloud_confirmed)`: start a summary and return `RUNNING` at once; the model runs on a job thread. A saved summary is replaced only on success | `MEETING_NOT_PROCESSED`; `SUMMARY_NO_SPEECH`; `SUMMARY_CLOUD_DISABLED`; `SUMMARY_CLOUD_NOT_CONFIRMED`; `SUMMARY_CLOUD_KEY_MISSING`, `SUMMARY_CLOUD_KEY_LOCKED`, `SUMMARY_CLOUD_KEY_UNAVAILABLE`; `SUMMARY_GPU_BUSY`; `SUMMARY_ALREADY_RUNNING`; `SUMMARY_BUSY`; `INVALID_VALUE` (unknown provider); `ENTITY_NOT_FOUND_MEETING`; `MEETING_SCHEMA_UNSUPPORTED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `CancelSummary` | method | `(s) -> b` | Cancel the running summary of the meeting: `true` when one was running, `false` otherwise (not an error). The outcome arrives as `SummaryFinished` with `CANCELLED` | `ENTITY_NOT_FOUND_MEETING` |
| `GetSummaryStatus` | method | `(s) -> a{sv}` | No content. `state` (s: `NONE`, `RUNNING`, `DONE`, `FAILED`), `stale` (b), `cloud` (b), `provider_label` (s), `generated_at` (s, empty when none), `step` (i), `total_steps` (i), `failure_code` (s, empty when none). `FAILED` means the last attempt failed; a previous `summary.md` may still exist (`generated_at` non-empty) | `ENTITY_NOT_FOUND_MEETING`; `MEETING_SCHEMA_UNSUPPORTED`; `MEETING_CORRUPT`; `MEETING_STORAGE_FAILED` |
| `GetSummary` | method | `(s) -> a{sv}` | The text and facts: `markdown` (s), `title` (s), `generated_at` (s), `provider_label` (s), `cloud` (b), `model` (s), `language` (s), `stale` (b). No path | `ENTITY_NOT_FOUND_SUMMARY`; `ENTITY_NOT_FOUND_MEETING`; `SUMMARY_STORAGE_FAILED` (unreadable file); the lookup errors of `GetSummaryStatus` |
| `LastSummary` | method | `() -> a{sv}` | The newest summary by `generated_at` whose `summary.md` still exists: `meeting_id` (s), `started_at` (s, the meeting's start), `generated_at` (s), `path` (s, absolute). For the panel menu, which opens the file with the system handler and never builds a path | `ENTITY_NOT_FOUND_SUMMARY` (none); `MEETING_STORAGE_FAILED` |
| `ActiveSummaryMeetingId` | property | `s` (read) | The meeting being summarized; empty when idle | n/a |
| `SummaryProgress` | signal | `(sii)` | `(meeting_id, step, total_steps)` after a step, at most one per second (the last step of a run is always sent) | n/a |
| `SummaryFinished` | signal | `(sss)` | `(meeting_id, state, error_code)` when a summary ends, `state` `DONE`, `FAILED` or `CANCELLED`; sent after the result is saved and the slot is free | n/a |

`PropertiesChanged` is emitted with `ActiveSummaryMeetingId` when it changes. Error messages carry ids and setting
names, never transcript text, speaker names, summary text or keys. Clients refresh `GetSummaryStatus` on
`SummaryFinished` and on `SpeakersChanged` (a rename may make the summary stale). The cloud label comes from the service
(`Providers`); no client builds it. A service shutdown cancels a running summary without a `SummaryFinished` guarantee.

Example calls (documentation only, run them against your own session bus; `Summarize` with `local` sends the transcript
to the local model only, with `cloud` it needs `true` as the last argument and sends it off this computer):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Summaries1.Providers
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Summaries1.GetSummaryStatus "2026-10-09_101500"
```

---

### `dev.meetingscribe.Settings1`

The few settings the user may change (summary provider, models and language, the meetings folder), owned by the
service: clients never read the settings file and never write `.env`. Same bus name and object path as `Recording1`.
Nothing here carries the cloud API key, which is not a setting and never crosses D-Bus (BR-005). Spec:
[preferences](../specs/preferences.md) sections 2 to 5.

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `GetAll` | method | `() -> aa{sv}` | One row per exposed field, in registry order: `key` (s, for example `summaries.provider`), `kind` (s: `choice`, `text`, `path`), `value` (s, the effective value), `default` (s), `source` (s: `default`, `file`, `env`, `dotenv`; the layer that wins), `locked` (b, true when `env` or `dotenv` supplies it), `lock_reason` (s, empty or a code such as `LEGACY_SETTING`), `apply` (s: `live`, `restart`), `choices` (as, empty unless `choice`), `file_problem` (s, empty or `SETTINGS_FILE_INVALID` when the saved file could not be read; the same on every row), `apply_problem` (s, empty or `SETTINGS_APPLY_FAILED` when the last save was kept but applying a live change raised; the same on every row, cleared by the next save whose apply succeeds). Values are strings; the client parses by `kind` | none |
| `Set` | method | `(a{ss}) -> a{sv}` | Validate the whole candidate (key to text) over the current values; on success write the settings file atomically, apply and signal. Returns `applied` (as, the keys written; empty on any error), `restart_required` (as, every key saved but not yet in effect, not only this call's), `apply_failed` (b, true when the values were saved and signalled but applying a live change raised: a restart picks them up) and `errors` (a(sss): key, code, message; one row per invalid field). An empty candidate is a no-op that writes nothing. All or nothing: any error means nothing is written, applied or signalled. Only one `Set` runs at a time; a second waits | `SETTINGS_STORAGE_FAILED` |
| `Restart` | method | `() -> s` | Restart `meeting-scribe.service` through the user systemd manager when nothing is active; returns `RESTARTING` and asks systemd after the reply is sent (`org.freedesktop.systemd1.Manager.RestartUnit("meeting-scribe.service", "replace")`). Unfinished jobs resume as on any start | `SETTINGS_RESTART_BUSY`; `SETTINGS_RESTART_UNAVAILABLE` |
| `RestartRequired` | property | `as` (read) | Keys saved but not yet in effect (the meetings folder after a change) | n/a |
| `SettingsChanged` | signal | `(as)` | The keys written by a `Set`, emitted after the file is saved. `PropertiesChanged` carries `RestartRequired` when it changes | n/a |

Field error codes inside `Set` replies: `FIELD_UNKNOWN`, `FIELD_LOCKED`, `FIELD_INVALID`, `FIELD_FOLDER_NOT_WRITABLE`
(see `error-codes-catalog.md`). Messages carry keys and next steps, never values: a value can hold a home path, and
logs and signals follow the same rule. A client that gets `UnknownMethod` or `UnknownInterface` treats the service as too
old and asks the user to update it. `LocalModels` (installed Ollama models) is planned (spec task P8) and not part of
this contract yet.

Example calls (documentation only, run them against your own session bus; `Set` changes your real settings):

```bash
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Settings1.GetAll
gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service \
  --method dev.meetingscribe.Settings1.Set "{'summaries.language': 'es'}"
```

---

## Out-of-scope members (documented for visibility)

| Member | Status | Reason |
|---|---|---|
| `dev.meetingscribe.Speakers1` | planned | The domain exists; only its D-Bus adapter is missing (the review members live in `Meetings1`) |

---

## How to add a new method, property or signal

1. Update this doc FIRST with the expected request/response.
2. Write the failing test (RED).
3. Define the interface adapter in the domain's `infrastructure/dbus/` (`ServiceInterface` subclass).
4. Implement the member; call the application controller and map its `Result` to a return value or a D-Bus error.
5. Add the new error codes to `error-codes-catalog.md`.
