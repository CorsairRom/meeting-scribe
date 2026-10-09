---
type: decision
adr_number: 004
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "All meeting data lives under STORAGE_MEETINGS_DIR. Persistence goes through the MeetingRepository port; domains never touch paths directly."
  - "meeting.json always carries schema_version. Caches must be keyed on processing parameters."
---

# ADR-004: One folder per meeting with a versioned `meeting.json`

## Status

`active` — approved by the user in the F0 review (2026-10-09).

## Context

Each meeting produces audio chunks, word-level transcription, diarization turns, voice samples, speaker names
and a summary. The data is private (BR-002), single-user, and must survive crashes. The prototype already used a
folder per session with cached `words.json` and `diarization.json`, which worked but had a cache bug: results
were reused even when processing parameters changed (GO-012).

## Options Considered

### Option A: SQLite database

**Description**: metadata, transcript and speakers in SQLite; audio on disk.

**Pros**:
- Fast listing, filtering and search.
- Transactions and a real schema.

**Cons**:
- Data is opaque without tooling; harder to inspect, back up selectively or repair by hand.
- Schema migrations from day one.
- Overkill while the catalog is small and single-user.

**Cost**: moderate now, with a migration burden for every model change.

### Option B: Folder per meeting with JSON files

**Description**: `<STORAGE_MEETINGS_DIR>/<YYYY-MM-DD_HHMMSS>/` containing a versioned `meeting.json`, audio chunks,
cached `words.json` and `diarization.json`, voice samples and the summary, behind a `MeetingRepository` port.

**Pros**:
- Inspectable with a text editor; trivially backed up, moved or deleted.
- Matches the prototype layout; crash recovery is just reading files.
- No server or migration tooling.

**Cons**:
- Listing and search scan folders (fine for hundreds, slow for thousands).
- No transactions: writes must be atomic (write to temp, then rename).

**Cost**: low.

### Option C: Single JSON index plus folders

**Description**: one global catalog file pointing to folders.

**Pros**:
- Fast listing without scanning.

**Cons**:
- A second source of truth that can drift from the folders; global write contention.

**Cost**: low, but introduces a consistency problem for little benefit.

---

## Decision

**We choose Option B.** SQLite remains the planned evolution if listing or search needs it (IDEA-003); the
`MeetingRepository` port keeps that change local.

Inspectability and simple backup outweigh query speed for a single-user tool, and the folder layout is already proven.

---

## Consequences

### Positive

- Transparent, portable data; easy debugging.
- No database to run, migrate or corrupt.
- Repository port isolates the format from domains.

### Negative

- Scan-based listing will not scale indefinitely.
- Atomic writes and partial-write recovery are our responsibility.

### Accepted trade-offs

- No ad-hoc queries across meetings in v1.

### Trigger to revisit

- Listing or search latency becomes noticeable (hundreds of meetings).
- Cross-meeting features (chat with the meeting, IDEA-002) need an index.

---

## Implementation notes

- Default root: `~/Documents/meetings/<YYYY-MM-DD_HHMMSS>/`, configurable via `STORAGE_MEETINGS_DIR`. The `~` expansion is a F1 follow-up (validator, TDD).
- Layout (planned): `meeting.json`, `audio/chunk_NNNNN.wav`, `words.json`, `diarization.json`, `samples/`, `summary.md`.
- `meeting.json` has `schema_version` (integer); the repository migrates older versions on read.
- **Cache invalidation**: store the processing parameters (model, language, VAD options, diarization settings, `num_speakers`) next to each cached artifact and invalidate when they differ. The prototype reused `words.json` regardless of params (GO-012).
- Writes are atomic (temp file + `os.replace`); never leave a half-written `meeting.json`.
- The repository returns a null object, not `None`, for a missing meeting (CR-005).
- Nothing in these folders is ever committed (`.gitignore` covers audio and cache files; BR-002).
- Tests: repository contract tests against a temp directory with synthetic data only; fake repository for domain tests.

---

## References

- `PRD.md` NFR-2, NFR-5
- [`../project/rules-registry.md`](../project/rules-registry.md): BR-002, CR-005, GO-012
- [`../ideas/README.md`](../ideas/README.md): IDEA-003
- [ADR-003](003-process-after-stop-single-gpu-worker.md)
