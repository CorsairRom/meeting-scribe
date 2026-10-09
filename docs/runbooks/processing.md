---
type: runbook
status: active
last_updated: 2026-10-09
phase: F3
verifiable_by:
  - scripts/install.sh
  - src/meeting_scribe/prefetch.py
  - src/meeting_scribe/cli.py
  - src/meeting_scribe/meetings/infrastructure/subprocess_worker_runner.py
  - tests/unit/test_install_scripts.py
  - tests/unit/test_prefetch.py
ai_instructions:
  - "Processing reads real meeting audio. An agent must NEVER open the meetings directory or run a real job on its own."
  - "Never read or print the Hugging Face token; the login is the user's one-time manual step."
---

# Set up and troubleshoot processing

## Purpose

Get meetings transcribed and diarized after Stop: what to install, what `make install` downloads, what to expect on
the first run, and what each failure code means. Design: [`../specs/processing.md`](../specs/processing.md).

## Preconditions

- An NVIDIA GPU with a working driver (`nvidia-smi` lists it). The CUDA libraries come from Python wheels; the
  driver is the only system part. Without it, jobs fail with `GPU_UNAVAILABLE` (never a silent CPU fallback).
- A Hugging Face account with the community-1 terms accepted. The diarization model is **gated**:
  1. Accept the terms at <https://hf.co/pyannote/speaker-diarization-community-1> while logged in.
  2. Log in once in a real terminal: `hf auth login` (the CLI comes with `huggingface_hub`; run it from the
     installed environment or any environment that has it). The token is stored by Hugging Face; this program never
     reads or prints it.
- Network access during `make install` only. Processing itself runs offline (`HF_HUB_OFFLINE=1`).

## Steps

1. `make install`. It installs the service with the `ml` extra (faster-whisper, torch, pyannote.audio) and then runs
   `meeting-scribe prefetch-models` as a visible step. That command downloads into the Hugging Face cache
   (`~/.cache/huggingface`, or `$HF_HOME`):
   - the diarization pipeline `pyannote/speaker-diarization-community-1` (`SPEAKERS_PIPELINE`) with the models it names,
   - the Whisper model `large-v3-turbo` (`TRANSCRIPTION_MODEL`).

   Approximate size: the Python environment is several GB (torch with CUDA) and the Whisper model about 1.6 GB.
   These are estimates from the spec, to be confirmed on the first real install (A-10). Later installs reuse the uv
   cache and the Hugging Face cache.
2. Read the summary. The `models:` line says `downloaded`, or `NOT downloaded` with the fix. A failed download never
   fails the service install: recording works, processing does not until the models are cached. The download is
   bounded by `MEETING_SCRIBE_PREFETCH_TIMEOUT` (a positive integer of seconds, default 3600; an invalid value warns and uses the default; the summary says when it timed out), and it is
   skipped when the install rolled back because the new version failed to start (fix that first).
3. If the models were not downloaded: fix the cause (login, terms, network; the download output above names it),
   then run `make install` again. To download only the models: `meeting-scribe prefetch-models` (installed path:
   `~/.local/share/meeting-scribe/current/bin/meeting-scribe prefetch-models`).

## First run

Stop a recording. The service queues the meeting, a worker transcribes it, a second worker diarizes it, and a desktop
notification says `Meeting processed: N speakers` or `Meeting processing failed` with the reason. The first job
loads the models from the cache, which takes noticeably longer than later ones. The panel menu shows the stage and the
queue while it runs.

Outputs are written into the meeting folder under `STORAGE_MEETINGS_DIR`: `meeting.json` (state), `words.json` and
`turns.json` (caches), `utterances.json`, `transcript.md`, `samples/` (voice clips) and `processing.log` (worker
stderr; this log can contain library messages, never read it into a shared place). Never open these files on someone
else's behalf; they are private.

## Reprocess

A `FAILED` (or `DONE`) meeting can be queued again. Valid caches are reused, so after fixing a diarization problem the
transcription is not repeated.

- Panel menu: **Processing failed - Reprocess** (shown for the last failed job until the next job starts).
- By hand: `gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service --method dev.meetingscribe.Meetings1.Reprocess "<meeting-id>"`.

Unfinished meetings resume by themselves when the service starts.

## Troubleshooting

| Code | Meaning | Fix |
|---|---|---|
| `GPU_UNAVAILABLE` | No usable CUDA device or driver | Fix the NVIDIA driver (`nvidia-smi`), restart the service, Reprocess. `PROCESSING_DEVICE=cpu` runs on the CPU explicitly (slow). |
| `GPU_OUT_OF_MEMORY` | The GPU ran out of memory | Close other GPU programs and Reprocess. There is no automatic CPU retry. |
| `DIARIZATION_MODEL_ACCESS_DENIED` | Terms not accepted or no Hugging Face login | Accept the terms at the model page, run `hf auth login`, run `make install` again, then Reprocess. Transcription is kept. |
| `MODEL_UNAVAILABLE` | A model is not in the cache (the worker is offline) | Run `make install` again (it downloads the models), or check `TRANSCRIPTION_MODEL` / `SPEAKERS_PIPELINE`, then Reprocess. |
| `WORKER_TIMEOUT` | A worker exceeded its stage timeout | Reprocess. For very long meetings raise `PROCESSING_WORKER_TIMEOUT_FACTOR` or `PROCESSING_WORKER_MIN_TIMEOUT_SECONDS`. |
| `WORKER_CRASHED` | A worker exited abnormally | Read the end of `processing.log` in the meeting folder (yourself, privately), then Reprocess. |
| `WORKER_PROTOCOL_ERROR` | A worker spoke an unexpected protocol | Reinstall with `make install` (service and worker must be the same version). |
| `MEETING_AUDIO_MISSING` | The recording has no audio chunk with frames | Nothing to process; the recording itself failed. |
| `MEETING_CORRUPT` / `MEETING_SCHEMA_UNSUPPORTED` | `meeting.json` is damaged or from a newer version | The meeting is never processed or rewritten; restore the file or upgrade the service. |
| `MEETING_STORAGE_FAILED` | The meetings folder could not be read or written | Check disk space and permissions; the job is retried a few times and can be Reprocessed. |
| Notification "Meeting could not be queued" | The recording is saved but its processing could not be queued (the text points at the log, it promises no retry) | Read `journalctl --user -u meeting-scribe.service`, fix the cause (disk, permissions), then restart the service: a `meeting.json` that exists is resumed at start, a folder without one is not rediscovered. |
| Service exits 4 | The processing loop did not stop at shutdown | See [`record-via-gdbus.md`](record-via-gdbus.md) troubleshooting and GO-026. |

Every code is listed in [`../architecture/error-codes-catalog.md`](../architecture/error-codes-catalog.md).

## Verify

`gdbus introspect --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service` lists
`dev.meetingscribe.Meetings1`. A real end-to-end check records audio and needs your explicit action: the agents never
do it.
