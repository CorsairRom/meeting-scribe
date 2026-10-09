---
type: decision
adr_number: 007
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null   # refines ADR-003's implementation notes; ADR-003's decision stands
superseded_by: null
ai_instructions:
  - "If a decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Keep the history intact: ADRs are architectural archaeology."
  - "The service process never imports torch, torchaudio, faster_whisper, ctranslate2 or pyannote. GPU work runs in a short-lived worker process, one per GPU stage of a job."
  - "Do not build on this ADR until its status is active."
---

# ADR-007: Run GPU work in short-lived worker processes, one per GPU stage of a job

## Status

`active` — approved by the user on 2026-10-09 (F3 design review).

## Context

[ADR-003](003-process-after-stop-single-gpu-worker.md) decided to process after stop, one GPU job at a time, loading
each model lazily and releasing it before the next. Its implementation notes say how to release: drop references,
`gc.collect()`, `torch.cuda.empty_cache()`. Mapping the prototype and the libraries showed that this is not enough to
promise "the GPU is free when we are not processing":

- PyTorch's `empty_cache()` releases only **unused** cached blocks; memory still referenced by tensors stays
  allocated, and tools like `nvidia-smi` show cached memory as used (PyTorch notes, memory management).
- The CUDA context of a process (hundreds of MB, assumption A-1 in the spec) is not released by any of these calls.
- Whisper runs on CTranslate2, which has its own GPU allocator: `torch.cuda.empty_cache()` cannot touch it, and
  whether `del model` returns everything is not documented (assumption A-2).
- The prototype loaded faster-whisper and pyannote in one process and never freed either (GO-010).

The service is also a long-lived D-Bus process on one asyncio loop (GO-024). Importing torch and the ML stack into it
adds seconds of start time and GBs of address space to a process that otherwise only supervises ffmpeg, and a native
crash (CUDA error, segmentation fault in a kernel, OOM kill) would take the D-Bus API and an active recording down
with it.

If we do not decide, F3.2 and F3.3 would each pick their own release strategy and the VRAM promise of NFR-3 would
depend on library internals.

## Options Considered

### Option A: In-process lazy load, then `del` + `gc.collect()` + `torch.cuda.empty_cache()`

**Description**: the service loads each model in a thread off the event loop when its stage starts and drops it when
the stage ends (ADR-003's original notes).

**Pros**:
- Simplest code: function calls, no protocol.
- No process start cost.

**Cons**:
- Does not reliably return VRAM: the CUDA context and any leaked reference stay; CTranslate2's allocator is outside
  `empty_cache()`.
- The service imports torch, CTranslate2 and pyannote: slower start, large memory footprint, idle GPU context.
- A native crash or OOM kill in a model kills the D-Bus service and any active recording.
- Long CPU-bound work in threads competes with the event loop (GIL), so D-Bus calls can stall.

**Cost**: low to build; recurring risk on the 8 GB GPU and on service stability.

### Option B: One long-lived worker process

**Description**: the service starts a worker once; it receives jobs over a pipe and loads and frees models per stage
(as in Option A, but in another process).

**Pros**:
- Crash isolation from the service.
- Process start cost paid once.

**Cons**:
- Same release problem as Option A inside the worker: its CUDA context and allocator caches persist while idle.
- Needs its own lifecycle management (health checks, restart, stuck-state recovery) and a richer protocol.
- An idle worker still holds GPU memory, which defeats NFR-3 while the user is in a meeting.

**Cost**: moderate, and the main problem (VRAM while idle) remains.

### Option C: Short-lived worker process per GPU stage of a job (recommended)

**Description**: for each job the service spawns `python -m meeting_scribe.worker transcribe`, which loads Whisper,
writes `words.json` and exits; then `python -m meeting_scribe.worker diarize`, which loads pyannote, writes the
diarization, releases it, extracts voice samples and exits. The job spec goes in as one JSON object on stdin;
progress and the result come back as JSON lines on stdout; the exit code confirms the outcome
([spec section 6.2](../specs/processing.md#62-worker-protocol-version-1)).

**Pros**:
- Process exit returns every byte of GPU memory, including the CUDA context and any allocator cache, whatever the
  library does internally. Whisper and pyannote are never resident together.
- The service never imports the ML stack: fast start, small footprint, no GPU context while idle.
- A native crash or OOM kill becomes a `WORKER_CRASHED` job failure; the D-Bus API and recording keep running.
- Timeouts and cancellation are plain process control (SIGTERM, grace, SIGKILL), already proven with ffmpeg (GO-020).
- torch (cu130) and CTranslate2 (cu12 libraries) never share a process, so GO-002 stops mattering in the product.

**Cons**:
- Interpreter start, imports and CUDA initialization are paid twice per job (assumption A-3: a few seconds against
  about 3 minutes for a 55-minute meeting).
- A protocol to define and test (events, exit codes, stdout hygiene).
- Stage results must go through files (the caches ADR-004 already requires), not return values.

**Cost**: moderate; the protocol is small and testable with a fake worker script.

A variant of Option C runs one process per job (Whisper, then pyannote, in the same process). It halves the start
cost but brings back the in-process release question between the two models (assumption A-2), so it is not chosen.

---

## Decision

**We choose Option C.**

Process exit is the only release mechanism that does not depend on undocumented library behaviour, and it gives
crash isolation and a light service for free. The start cost is small against the processing time and is the same
kind of cost ADR-003 already accepted ("model load time is paid per job"). Spawning is done with
`asyncio.create_subprocess_exec(sys.executable, "-m", "meeting_scribe.worker", <stage>)` rather than
`multiprocessing` (spawn), which re-imports the parent's main module, pickles arguments and fits the asyncio loop
less naturally than a child process with pipes.

---

## Consequences

### Positive

- VRAM is back to the desktop baseline whenever no worker runs (verifiable with `nvidia-smi`, opt-in GPU test).
- The D-Bus service stays responsive and survives any model crash.
- Worker logic is testable in isolation through its protocol; the service is testable with a fake worker.

### Negative

- Two process starts per job.
- A second composition root (`meeting_scribe/worker.py`) to keep consistent with `main.py`.
- Worker stdout is reserved for the protocol: the worker must redirect file descriptor 1 to stderr before importing
  any library, or a stray `print` corrupts the stream.

### Accepted trade-offs

- A few seconds of start-up per stage in exchange for a hard guarantee on GPU memory.
- Results travel through files in the meeting folder rather than in memory.

### Trigger to revisit

- Measured worker start-up exceeds about 10% of total processing time for a typical meeting.
- Live transcription (IDEA-001) needs a model kept warm.
- A library documents a reliable in-process release that also frees the CUDA context.

---

## Implementation notes

- Affected files (planned): `src/meeting_scribe/worker.py` (composition root, `python -m` entry point);
  `src/meeting_scribe/meetings/application/` (stage use cases, processing controller);
  `src/meeting_scribe/meetings/domain/` (`WorkerRunner` port, worker errors);
  `src/meeting_scribe/meetings/infrastructure/subprocess_worker_runner.py`; transcription and speakers adapters
  importing their ML library lazily.
- Import contracts: a new forbidden contract keeps `meeting_scribe.main` free of `torch`, `torchaudio`,
  `faster_whisper`, `ctranslate2` and `pyannote`; contract 6 is extended to `meeting_scribe.worker`.
- The worker reads no settings: the service sends the resolved parameters in the job spec.
- Spawn with `start_new_session=True`; terminate by process-group id (SIGTERM, grace, SIGKILL), never by pattern
  (GO-007). Worker stderr is appended to `processing.log` in the meeting folder; no transcript text is logged.
- CUDA library preload with logging stays in the transcribe worker (GO-001, GO-011).
- Tests: a fake worker script driven by scripted events (crash, timeout, protocol error, success); real models only
  in opt-in `@pytest.mark.gpu` tests.
- Related docs: [processing spec](../specs/processing.md), [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)
  sections 4 and 5, [rules registry](../project/rules-registry.md) (GO-001, GO-002, GO-010, GO-011).

---

## References

- [ADR-003](003-process-after-stop-single-gpu-worker.md) (this ADR refines how its "release between stages" is done)
- [ADR-004](004-folder-per-meeting-json-storage.md) (stage results are the keyed caches in the meeting folder)
- `PRD.md` NFR-3, NFR-5
- PyTorch notes, memory management: https://github.com/pytorch/pytorch/blob/main/docs/source/notes/hip.rst and
  https://github.com/pytorch/pytorch/blob/main/docs/source/notes/cuda.md
- faster-whisper README (GPU requirements): https://github.com/systran/faster-whisper/blob/master/README.md
