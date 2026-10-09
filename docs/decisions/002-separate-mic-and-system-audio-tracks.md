---
type: decision
adr_number: 002
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Capture writes 2-channel chunks: channel 0 = microphone, channel 1 = system monitor. Never mix them to mono before diarization."
  - "The 'Me' decision must use per-turn energy dominance, not blind channel assignment. The exact algorithm is specified in the F3 spec."
---

# ADR-002: Record microphone and system audio as separate, synchronized tracks

## Status

`active` — approved by the user in the F0 review (2026-10-09).

## Context

The prototype mixes the microphone and the system output monitor into one mono stream with `amix`. Diarization
then has to separate every voice, including the user's own, from a single signal, and the result cannot say which
speaker is the local user. In practice one real person was split into two speakers in a 55-minute meeting.

If the two sources were kept apart, the user's own voice would come from the microphone track, and diarization
would only need to separate the remote participants on the system track. But a naive split has a trap:
when the user listens through loudspeakers instead of a headset, system audio bleeds into the microphone.

## Options Considered

### Option A: Mono `amix` (prototype)

**Description**: mix both inputs into one 16 kHz mono stream and chunk it.

**Pros**:
- Simplest capture; proven.
- One file per chunk.

**Cons**:
- Loses who-is-who: no way to label the local user.
- Diarization works harder on a mixed signal; over-splitting observed.

**Cost**: none, but the quality ceiling is the problem.

### Option B: Two independent ffmpeg processes and files

**Description**: one ffmpeg per source, each writing its own chunk files.

**Pros**:
- Trivial per-source pipelines.
- Failure of one source does not stop the other.

**Cons**:
- Two clocks: start offsets and drift between tracks accumulate over a long meeting.
- Chunk boundaries do not line up; re-synchronizing is error-prone.
- Double the process management (signals, `q` on stdin, kill fallback).

**Cost**: moderate, plus a recurring synchronization risk.

### Option C: One ffmpeg process writing 2-channel chunks

**Description**: one ffmpeg reads both PipeWire sources and combines them as channels (`amerge` or `join`):
channel 0 = microphone, channel 1 = system monitor, written into the same 30 s segments.

**Pros**:
- Both tracks are sample-synchronized by construction and share chunk boundaries.
- One process to supervise.
- Keeps the 30 s chunk durability model (ADR-003).

**Cons**:
- If one source is missing or stalls, the filter graph needs a defined fallback (for example silence on that channel).
- Chunks are 2-channel, so the transcription adapter must select a channel instead of reading mono directly.

**Cost**: low to moderate; one filter graph to design and test.

---

## Decision

**We choose Option C.**

Synchronization is the hard part of separate tracks, and one process writing both channels removes it. It also
preserves the crash-safe chunking of ADR-003.

---

## Consequences

### Positive

- The local user's voice is identifiable (auto-label "Me").
- Diarization runs on the system channel only, where the remote participants are.
- Better separation than a mono mix.

### Negative

- Chunk format is 2-channel; consumers must pick a channel.
- Capture has more failure modes (missing mic, missing monitor) that need explicit handling.

### Accepted trade-offs

- **Loudspeaker bleed**: with speakers instead of a headset, system audio leaks into the microphone channel. "Me"
  must therefore not be assigned blindly to the microphone channel. It is decided **per speech turn by energy
  dominance** (mic energy versus system energy over the turn, with margin and a minimum duration). The exact
  algorithm, thresholds and test fixtures are specified in the F3 spec before implementation.
- Some turns in ambiguous conditions will be labelled "unknown" instead of guessed.

### Trigger to revisit

- Dominance-based labelling mislabels more than an agreed fraction of turns on the user's real setups.
- PipeWire offers a cleaner echo-cancelled capture source that makes the split unnecessary.
- ffmpeg cannot hold both sources in sync over multi-hour meetings.

---

## Implementation notes

- Domain: `recording` (capture adapter in `infrastructure/`, port in `domain/`); consumers in `transcription` and `speakers` receive audio through their own ports, never importing `recording` (CR-001); `meetings` wires them.
- Devices from `pactl get-default-sink` (`<sink>.monitor`) and `pactl get-default-source`.
- Output format: 16 kHz, s16le, 2 channels, `-f segment -segment_time 30 -reset_timestamps 1`.
- Process control per GO-006: `start_new_session=True`, stop with `q` on stdin, kill fallback on timeout, explicit signal handlers.
- Transcription: decode WAV with `wave` + numpy (GO-003) and transcribe a mono signal derived for ASR; diarize the system channel.
- Tests: synthetic 2-channel WAVs (tone on one channel, silence on the other) for channel selection and for the dominance rule. Never real audio.
- Docs to update when built: `docs/specs/` (labelling spec), `docs/project/rules-registry.md`.

---

## References

- `PRD.md` FR-3, open question 1
- [`../project/rules-registry.md`](../project/rules-registry.md): GO-003, GO-006, GO-009
- [ADR-003](003-process-after-stop-single-gpu-worker.md)
