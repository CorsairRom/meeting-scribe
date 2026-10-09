---
type: decision
adr_number: 003
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "No GPU work during recording. Only one GPU job at a time. Release each model before loading the next."
  - "Audio is persisted in 30 s chunks while recording so a crash never loses the meeting."
---

# ADR-003: Process after stop with a single GPU worker; no live transcription in v1

## Status

`active` — approved by the user in the F0 review (2026-10-09).

## Context

Transcription (faster-whisper large-v3-turbo) and diarization (pyannote) both want the GPU. The reference GPU has
8 GB and is also driving the desktop and the meeting application itself. The prototype loaded both models at once
and never freed them. A crash during a meeting must not lose audio. Offline processing of a 55-minute meeting took
about 3 minutes, so waiting after stop is cheap.

## Options Considered

### Option A: Live transcription during recording

**Description**: run Whisper on chunks as they arrive and show text in real time.

**Pros**:
- Text available during the meeting.
- No wait after stop.

**Cons**:
- GPU contention with the meeting app and the desktop on an 8 GB card.
- Short isolated windows give worse accuracy than whole-session processing (and weaker word timing for diarization).
- Must still run diarization after the fact, so the work is duplicated.

**Cost**: high complexity for little gain in the stated use case.

### Option B: Process after stop, models resident

**Description**: after stop, transcribe then diarize with both models kept loaded (the prototype's behavior).

**Pros**:
- Simplest code.
- Fast follow-up jobs.

**Cons**:
- Whisper and pyannote together pressure 8 GB; out-of-memory risk, and GPU memory held while idle.

**Cost**: low, but fragile and wasteful.

### Option C: Process after stop, queued, single GPU worker, lazy load and release

**Description**: persist 30 s chunks on disk during recording; on stop, enqueue a job; one worker runs one GPU job
at a time; load each model when its stage starts and release it before the next; resume pending jobs at service start.

**Pros**:
- Fits 8 GB comfortably; no GPU use during recording.
- Crash-safe: chunks on disk, jobs resumable.
- Whole-session processing gives the best quality.

**Cons**:
- Model load time is paid per job.
- A queue and a worker are more code than a direct call.

**Cost**: moderate; the queue is small and testable with fakes.

---

## Decision

**We choose Option C.** Live transcription is rejected for v1 (it stays an idea, IDEA-001).

It is the only option that respects the GPU budget and the "never lose audio" requirement while keeping quality.
The per-job model load time is small against the measured processing time.

---

## Consequences

### Positive

- Predictable GPU memory; no contention while the user is in a meeting.
- Crash recovery by design.
- Simple mental model: record, then process.

### Negative

- The transcript is not available until processing finishes.
- Model reload cost on every job.

### Accepted trade-offs

- No in-meeting captions in v1.
- A long meeting queued behind another waits; there is no parallel processing.

### Trigger to revisit

- Measured model load time becomes a noticeable fraction of processing time (consider keeping models warm for a short idle window).
- A clear need for in-meeting captions (IDEA-001) and a GPU with enough headroom.

---

## Implementation notes

- Recording: chunks `chunk_%05d.wav` written to the meeting folder as the meeting runs (ADR-002, ADR-004).
- Queue and worker: in `meetings` (`application`), a job entity with states and transitions as entity methods (CR-006). Port for "run a GPU stage" implemented by `transcription` and `speakers` adapters.
- Lifecycle per job: stop, enqueue, transcribe (Whisper loaded, then released), diarize (pyannote loaded, then released), extract voice samples, notify.
- GPU hygiene: free models explicitly (drop references, `gc.collect()`, `torch.cuda.empty_cache()` where applicable); log CUDA preload failures and any CPU fallback (GO-001, GO-011).
- Resume: on service start, scan meeting folders for unfinished jobs and re-enqueue.
- Cache: keyed on processing params (ADR-004, GO-012). Use real chunk durations for timing, not `index * 30 s` (GO-013).
- Notification: desktop notification "Done: N speakers" when the job completes.
- Tests: in-memory queue and fake GPU stages; no real models in unit tests.

---

## References

- `PRD.md` NFR-3, NFR-4, NFR-5, FR-3
- [`../project/rules-registry.md`](../project/rules-registry.md): GO-010, GO-011, GO-012, GO-013
- [`../ideas/README.md`](../ideas/README.md): IDEA-001
- [ADR-002](002-separate-mic-and-system-audio-tracks.md), [ADR-004](004-folder-per-meeting-json-storage.md)
