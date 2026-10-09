---
type: runbook
status: active
last_updated: 2026-10-09
phase: F1
verifiable_by:
  - docs/architecture/api-contracts.md
  - src/meeting_scribe/main.py
ai_instructions:
  - "This runbook records real audio. An agent must NEVER run it on its own: only the user runs these steps."
  - "Never read, open or quote the recorded chunks or the meetings directory."
---

# Record and stop a meeting via gdbus

## Purpose

Drive the F1 recording service by hand over the D-Bus session bus, without the GNOME extension, and check the
output format without listening to it.

## Privacy warning

> **This records your real microphone and the system audio** into `STORAGE_MEETINGS_DIR`
> (default `~/Documents/meetings`). Run it only knowingly, tell anyone who can be heard, never play the output back
> in a shared place, and **never commit the output** or paste its content anywhere. An AI agent must not run
> these steps for you.

## Preconditions

- `make setup` done.
- PipeWire (with the PulseAudio shim) running, and a real microphone set as the default source (not a monitor).
- `ffmpeg`, `ffprobe`, `pactl` and `gdbus` installed.
- Optional config in `${XDG_CONFIG_HOME:-~/.config}/meeting-scribe/.env` (template: `.env.example`).

## Steps

1. Terminal A, start the service in the foreground:

   ```bash
   make run
   ```

2. Terminal B, start recording (this opens the microphone; tell the people around you):

   ```bash
   gdbus call --session --dest dev.meetingscribe.Service \
     --object-path /dev/meetingscribe/Service \
     --method dev.meetingscribe.Recording1.Start
   ```

   The reply is the session id (a single string, e.g. `2026-10-09_111739`). Read the session
   directory from the `Directory` property in step 3.

3. Read the properties:

   ```bash
   gdbus call --session --dest dev.meetingscribe.Service \
     --object-path /dev/meetingscribe/Service \
     --method org.freedesktop.DBus.Properties.GetAll dev.meetingscribe.Recording1
   ```

4. Optional, in a third terminal, watch `StateChanged` and property signals:

   ```bash
   gdbus monitor --session --dest dev.meetingscribe.Service
   ```

5. Stop recording:

   ```bash
   gdbus call --session --dest dev.meetingscribe.Service \
     --object-path /dev/meetingscribe/Service \
     --method dev.meetingscribe.Recording1.Stop
   ```

## Verify

Inspect the chunks without playing them. Use the session directory from the `Directory` property (step 3):

```bash
ffprobe -hide_banner <session-dir>/chunk_00000.wav
```

Expect `2 channels` (mic left, system audio right), `16000 Hz`, `s16`. Chunks are 30 s by default
(`RECORDING_CHUNK_SECONDS`). Then stop the service with Ctrl+C in terminal A: it must exit with code 0
(`echo $?` right after).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Service exits 1 | Another instance owns `dev.meetingscribe.Service` (GO-026). Stop it (`systemctl --user stop meeting-scribe.service` if installed) and retry. |
| Service exits 2 | Stopping the active recording at shutdown failed; see the ERROR log line for its code. |
| Service exits 3 | The D-Bus session connection was lost; check the session bus and restart. |
| `AUDIO_DEVICE_NOT_FOUND` | No default microphone, or the default source is a monitor. Pick a real input: `pactl set-default-source <name>`. |
| `AUDIO_CAPTURE_FAILED` | ffmpeg failed to start or stop cleanly. Read `<session-dir>/ffmpeg.log`. |
| Settings seem ignored | Config is read from `${XDG_CONFIG_HOME:-~/.config}/meeting-scribe/.env` plus the process environment; the startup log line `config source:` says which was used. |

## Cleanup

Delete the test session folder (`rm -r <session-dir>`) once you have checked the format. Do not keep test audio.

## Install as a user service (F6)

Installation arrives in F6 (`make install`); nothing here installs anything. Two packaging files prepare it:

- `packaging/systemd/meeting-scribe.service.in`: the `systemd --user` unit template (`Type=dbus`, restart on failure
  but not on exit 1, ordered after PipeWire). `make install` will render `@EXEC_START@`.
- `packaging/dbus/dev.meetingscribe.Service.service`: the D-Bus activation file; it hands activation to
  `meeting-scribe.service`, so the first call on the bus starts the service.
