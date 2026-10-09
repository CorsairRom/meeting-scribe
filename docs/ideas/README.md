---
type: index
status: living
last_updated: 2026-10-09
ai_instructions:
  - "Ideas have no committed scope. Do NOT implement an idea unless it has become an ADR and is in mvp-scope.md."
  - "Use the IDEA-NNN format. When an idea becomes an ADR, mark it approved with the ADR number and close it."
---

# Ideas — meeting-scribe

Ideas that came up but have no scope yet. Each has a state: `new`, `evaluating`,
`approved -> became ADR-NNN`, or `discarded`.

Format for each entry:

```
## IDEA-NNN: <title>
**State:** new | evaluating | approved -> ADR-NNN | discarded
**Origin:** <where it came from, date>
### The idea
### Why it is interesting
### Open questions
### Risks
```

---

## IDEA-001: Live transcription

**State:** `new`
**Origin:** HANDOFF out-of-scope list, 2026-10-09

### The idea

Show a rolling transcript while the meeting is in progress instead of only after stop.

### Why it is interesting

In-meeting captions and the ability to glance back at what was just said.

### Open questions

1. Can an 8 GB GPU run streaming Whisper while the desktop and the meeting app also use it?
2. Is the quality of short streaming windows acceptable compared with whole-chunk processing?
3. How would live text interact with the post-stop diarization pass (reconcile or replace)?

### Risks

GPU contention; worse quality than offline processing (see [ADR-003](../decisions/003-process-after-stop-single-gpu-worker.md)).

---

## IDEA-002: Chat with the meeting

**State:** `new`
**Origin:** HANDOFF out-of-scope list, 2026-10-09

### The idea

Ask free-form questions about a processed meeting ("what did we decide about X?") answered from the transcript.

### Why it is interesting

Goes beyond a fixed summary structure; useful for long meetings and cross-meeting questions.

### Open questions

1. Single meeting or across the catalog (needs retrieval and probably SQLite, see IDEA-003)?
2. Local-only models, or reuse the `SummaryProvider` port with the cloud opt-in rules (BR-003, BR-004)?

### Risks

Each question sends transcript text to the provider; the explicit-action rule (BR-004) must hold per question.

---

## IDEA-003: SQLite storage

**State:** `new`
**Origin:** ADR-004 revisit trigger, 2026-10-09

### The idea

Index the meetings catalog in SQLite behind the existing `MeetingRepository` port while keeping audio and
artifacts as files.

### Why it is interesting

Fast listing, search by date, participant or text once there are hundreds of meetings.

### Open questions

1. Is `meeting.json` still the source of truth, with SQLite as a rebuildable index?
2. What is the migration path for existing folders?

### Risks

Two sources of truth if the index drifts; added schema migration burden.
