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

Get meetings transcribed and diarized after Stop: what to install, what the install needs and how the models get downloaded, what to expect on
the first run, and what each failure code means. Design: [`../specs/processing.md`](../specs/processing.md).

## Preconditions

- An NVIDIA GPU with a working driver (`nvidia-smi` lists it). The CUDA libraries come from Python wheels; the
  driver is the only system part. Without it, jobs fail with `GPU_UNAVAILABLE` (never a silent CPU fallback).
- A Hugging Face account with the community-1 terms accepted. The diarization model is **gated**:
  1. Accept the terms at <https://hf.co/pyannote/speaker-diarization-community-1> while logged in.
  2. Give the download a token: add it in Preferences (Speech models), or log in once in a real terminal with
     `hf auth login` (the CLI comes with `huggingface_hub`). The download uses the Preferences token when there is one
     and the `hf auth login` login otherwise. This program never prints either.
- Network access only while the models are downloaded. Processing itself runs offline (`HF_HUB_OFFLINE=1`) and needs
  neither a token nor a login: a cached model loads without them.

## Steps

1. `./install.sh` (alias `make install`). It runs the preflight, installs the service with the `ml` extra
   (faster-whisper, torch, pyannote.audio) and does **not** download the models: recording never needs them. The
   models live in the Hugging Face cache (`~/.cache/huggingface`, or `$HF_HOME`):
   - the diarization pipeline `pyannote/speaker-diarization-community-1` (`SPEAKERS_PIPELINE`) with the models it names,
   - the Whisper model `large-v3-turbo` (`TRANSCRIPTION_MODEL`).

   Size, measured on the first real install (A-10): about 7.1 GB per installed version (torch with CUDA) and about 1.6 GB
   of models in the Hugging Face cache (`large-v3-turbo` 1.6 GB, community-1 32 MB). Later installs reuse the uv
   cache and the Hugging Face cache. The preflight checks the free space per filesystem (`disk.space`).
2. Read the summary. The `models:` line says `ready (cached)`, or `PENDING: add your Hugging Face token in Preferences
   and press Download models` (an offline check, bounded by `MEETING_SCRIBE_MODELS_CHECK_TIMEOUT` seconds, default 30).
   Pending is not a failure: recording works, processing does not until the models are cached.
3. Fetch the models. The service exposes `dev.meetingscribe.Models1` (F6.5: `GetStatus`, `Download`, `CancelDownload`, signals
   `ModelsProgress` and `ModelsFinished`, see [`../architecture/api-contracts.md`](../architecture/api-contracts.md)); the Download
   models action in Preferences that drives it arrives with F6.6 and F6.7. From a terminal today:
   `gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service --method dev.meetingscribe.Models1.GetStatus`
   (and `...Models1.Download`; the service runs the download itself with the keyring token, else your `hf auth login` login, and
   stops it after `MODEL_SETUP_DOWNLOAD_TIMEOUT_SECONDS`, default 3600). Or run
   `meeting-scribe prefetch-models` (installed path: `~/.local/share/meeting-scribe/current/bin/meeting-scribe
   prefetch-models`) after the login and the terms above. Developer options of the same command:
   `--check` (offline; prints `{"whisper": true, "pipeline": false}` and exits 0 only when both are cached, a partial
   download never counts), `--token-stdin` (the token is the first line of standard input, never an argument) and
   `--progress-json` (one JSON object per line: `step`, `error`, `finished`). The service runs it that way.

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
| `DIARIZATION_MODEL_ACCESS_DENIED` | Terms not accepted, or no usable Hugging Face token | Accept the terms at the model page, add your token in Preferences (or run `hf auth login`), press Download models (or `meeting-scribe prefetch-models`), then Reprocess. Transcription is kept. |
| `MODEL_UNAVAILABLE` | A model is not in the cache (the worker is offline) | Open Preferences and press Download models (or run `meeting-scribe prefetch-models`; the install only reports `models: PENDING`), or check `TRANSCRIPTION_MODEL` / `SPEAKERS_PIPELINE`, then Reprocess. |
| `WORKER_TIMEOUT` | A worker exceeded its stage timeout | Reprocess. For very long meetings raise `PROCESSING_WORKER_TIMEOUT_FACTOR` or `PROCESSING_WORKER_MIN_TIMEOUT_SECONDS`. |
| `WORKER_CRASHED` | A worker exited abnormally | Read the end of `processing.log` in the meeting folder (yourself, privately), then Reprocess. |
| `WORKER_PROTOCOL_ERROR` | A worker spoke an unexpected protocol | Reinstall with `make install` (service and worker must be the same version). |
| `MEETING_AUDIO_MISSING` | The recording has no audio chunk with frames | Nothing to process; the recording itself failed. |
| `MEETING_CORRUPT` / `MEETING_SCHEMA_UNSUPPORTED` | `meeting.json` is damaged or from a newer version | The meeting is never processed or rewritten; restore the file or upgrade the service. |
| `MEETING_STORAGE_FAILED` | The meetings folder could not be read or written | Check disk space and permissions; the job is retried a few times and can be Reprocessed. |
| Notification "Meeting could not be queued" | The recording is saved but its processing could not be queued (the text points at the log, it promises no retry) | Read `journalctl --user -u meeting-scribe.service`, fix the cause (disk, permissions), then restart the service: a `meeting.json` that exists is resumed at start, a folder without one is not rediscovered. |
| Service exits 4 | The processing loop did not stop at shutdown | See [`record-via-gdbus.md`](record-via-gdbus.md) troubleshooting and GO-026. |

Every code is listed in [`../architecture/error-codes-catalog.md`](../architecture/error-codes-catalog.md).

## Verify the offline, token-free load

Proof that processing needs no token (only with the models already cached, never downloads, never audio):
`uv run pytest -m gpu -s tests/gpu/test_offline_cached_load.py`. It loads the cached pipeline in a subprocess with
`HF_TOKEN` unset, `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` and `HF_TOKEN_PATH` pointing at a file that
does not exist, so no token source is available and a successful load shows none is needed. The offline cache check is covered by `tests/unit/test_prefetch_check_offline.py`, which runs
`prefetch-models --check` on a fake cache in a throwaway HOME.

## Verify

`gdbus introspect --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service` lists
`dev.meetingscribe.Meetings1`. A real end-to-end check records audio and needs your explicit action: the agents never
do it.
