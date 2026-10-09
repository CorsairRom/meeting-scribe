---
type: architecture
status: active
last_updated: 2026-10-09
verifiable_by:
  - src/meeting_scribe/base/domain/errors.py
  - src/meeting_scribe/recording/domain/errors.py
  - src/meeting_scribe/meetings/domain/errors.py
  - tests/unit/meetings/test_errors.py
  - tests/unit/recording/test_errors.py
  - tests/unit/base/test_errors.py
ai_instructions:
  - "This catalog is the source of truth for stable error codes. Add every new error class here in the same commit."
  - "Codes are UPPER_SNAKE_CASE and never change once released; clients map on them."
  - "Controllers return Failure(<error>); D-Bus interface adapters translate the error into the D-Bus error below."
---

# Error Codes Catalog

## D-Bus error mapping

Every failed D-Bus method call returns a D-Bus error whose name embeds the stable `error_code`. The adapter raises
it with dbus-fast as `DBusError("dev.meetingscribe.Error.<ERROR_CODE>", message)`:

| Part | Value | Description |
|---|---|---|
| Error name | `dev.meetingscribe.Error.<ERROR_CODE>` | Stable, machine-readable. Clients (extension, review app) map on this. Example: `dev.meetingscribe.Error.ENTITY_NOT_FOUND`. |
| Message | `string` | English debug description (for example `Meeting 'abc123' does not exist.`). Not intended for direct display. |

The error-name prefix is proposed and renamable until F6, together with the bus name
([ADR-001](../decisions/001-thin-gnome-extension-dbus-service.md)). Inspect errors with `gdbus call` or `busctl --user call`.

> **State today (F1.4)**: `to_dbus_error` (`src/meeting_scribe/base/infrastructure/dbus/errors.py`) builds this error
> from any `MeetingScribeError` using the instance `error_code` (so detail codes carry through); the `Recording1`
> adapter applies it to every `Failure`. Only the error message crosses the bus: no stack trace.

### Validation errors

Validation failures use `dev.meetingscribe.Error.INVALID_VALUE`; the failing field is named in the message
(for example `Field 'name' is required.`). D-Bus errors carry a single message string, so there is no structured
details payload.

---

## Defined codes

All classes derive from `MeetingScribeError` in `src/meeting_scribe/base/domain/errors.py`.

| Class | `error_code` | Meaning | D-Bus error name |
|---|---|---|---|
| `MeetingScribeError` | `INTERNAL_ERROR` | Base class and fallback for unexpected failures | `dev.meetingscribe.Error.INTERNAL_ERROR` |
| `EntityNotFoundError` | `ENTITY_NOT_FOUND` | A requested entity does not exist (typically a null object converted by `as_result`) | `dev.meetingscribe.Error.ENTITY_NOT_FOUND` |
| `InvalidStateTransitionError` | `INVALID_STATE_TRANSITION` | An entity transition is not allowed from its current state | `dev.meetingscribe.Error.INVALID_STATE_TRANSITION` |
| `InvalidValueError` | `INVALID_VALUE` | A value violates a domain invariant | `dev.meetingscribe.Error.INVALID_VALUE` |
| `RecordingAlreadyActiveError` | `RECORDING_ALREADY_ACTIVE` | `recording`: start requested while a recording is in progress (subclass of `InvalidStateTransitionError`) | `dev.meetingscribe.Error.RECORDING_ALREADY_ACTIVE` |
| `NoActiveRecordingError` | `NO_ACTIVE_RECORDING` | `recording`: stop requested with no recording in progress (subclass of `InvalidStateTransitionError`) | `dev.meetingscribe.Error.NO_ACTIVE_RECORDING` |
| `AudioDeviceNotFoundError` | `AUDIO_DEVICE_NOT_FOUND` | `recording`: default sink monitor or microphone source could not be resolved | `dev.meetingscribe.Error.AUDIO_DEVICE_NOT_FOUND` |
| `AudioCaptureError` | `AUDIO_CAPTURE_FAILED` | `recording`: the capture process failed to start or stop cleanly | `dev.meetingscribe.Error.AUDIO_CAPTURE_FAILED` |
| `RecordingStorageError` | `RECORDING_STORAGE_FAILED` | `recording`: the session folder could not be created (or already exists) | `dev.meetingscribe.Error.RECORDING_STORAGE_FAILED` |
| `MeetingStorageError` | `MEETING_STORAGE_FAILED` | `meetings`: `meeting.json` could not be read, written or listed (I/O error or corrupt file) | `dev.meetingscribe.Error.MEETING_STORAGE_FAILED` |
| `MeetingSchemaUnsupportedError` | `MEETING_SCHEMA_UNSUPPORTED` | `meetings`: `meeting.json` has a `schema_version` newer than the service supports; the meeting is never processed or rewritten | `dev.meetingscribe.Error.MEETING_SCHEMA_UNSUPPORTED` |
| `MeetingProcessingActiveError` | `MEETING_PROCESSING_ACTIVE` | `meetings`: reprocess requested while the job is queued or running (subclass of `InvalidStateTransitionError`) | `dev.meetingscribe.Error.MEETING_PROCESSING_ACTIVE` |
| `MeetingAudioMissingError` | `MEETING_AUDIO_MISSING` | `meetings`: the recording has no chunk with audio frames | `dev.meetingscribe.Error.MEETING_AUDIO_MISSING` |
| `MeetingAudioUnsupportedError` | `MEETING_AUDIO_UNSUPPORTED` | `meetings`: a chunk is not 2-channel, 16 kHz, 16-bit PCM | `dev.meetingscribe.Error.MEETING_AUDIO_UNSUPPORTED` |
| `WorkerCrashedError` | `WORKER_CRASHED` | `meetings`: a processing worker process exited abnormally | `dev.meetingscribe.Error.WORKER_CRASHED` |
| `WorkerTimeoutError` | `WORKER_TIMEOUT` | `meetings`: a processing worker exceeded its stage timeout | `dev.meetingscribe.Error.WORKER_TIMEOUT` |
| `WorkerProtocolError` | `WORKER_PROTOCOL_ERROR` | `meetings`: a worker emitted an unparseable or out-of-order event | `dev.meetingscribe.Error.WORKER_PROTOCOL_ERROR` |
| `WorkerReportedError` | instance code from the worker (`GPU_OUT_OF_MEMORY`, ...; class default `WORKER_REPORTED_ERROR`) | `meetings`: the worker's own `error` event, carried into `processing.failure` | the worker's code under `dev.meetingscribe.Error.` |
| `WorkerCancelledError` | `WORKER_CANCELLED` | `meetings`: the worker was stopped on request (preemption or shutdown); internal, the job is not failed and it never reaches a client | not exposed |

Domain packages add their own subclasses next to their domain code (for example a `recording` error) and
register them in this table.

---

## Naming convention

Codes follow `{ENTITY}_{ERROR_TYPE}` in `UPPER_SNAKE_CASE`. Common suffixes:

| Suffix | Meaning |
|---|---|
| `_NOT_FOUND` | Entity does not exist |
| `_ALREADY_EXISTS` | Entity already exists |
| `_VALIDATION_ERROR` | Business rule validation failed |
| `_CREATION_ERROR` / `_UPDATE_ERROR` | Persisting failed |

The D-Bus error name is always `dev.meetingscribe.Error.` plus the full code.

---

## Detail codes

When one error type can fail for several distinct reasons, pass `detail_code` to append a suffix to the
code **on that instance only**:

```python
error = EntityNotFoundError("Meeting 'abc' does not exist", detail_code="MEETING")
# error.error_code == "ENTITY_NOT_FOUND_MEETING"
return Failure(error)  # controllers return Failure, never raise (CR-004)
```

The rule, implemented in `MeetingScribeError.__init__`:

```
{BASE_ERROR_CODE}_{DETAIL_CODE}   # only when detail_code is given; otherwise just {BASE_ERROR_CODE}
```

`detail_code` must match `^[A-Z][A-Z0-9_]*$`. Anything else, including the empty string, raises `ValueError`
at construction instead of being silently ignored.

Clients can then:

1. **Match broadly** on the base code (`ENTITY_NOT_FOUND`) for a generic message.
2. **Match specifically** on the full code (`ENTITY_NOT_FOUND_MEETING`) for a precise message.

Detail codes are optional and introduced incrementally as clients need finer messages. The class attribute
is never mutated, so the base code stays stable.

### Known detail codes

None yet.
