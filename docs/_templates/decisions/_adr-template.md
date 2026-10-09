---
type: decision
adr_number: XXX
status: proposed  # proposed | active | superseded | deprecated
date: YYYY-MM-DD
deciders: [richard, ai-assistant]
supersedes: null   # ADR-NNN if this replaces another decision
superseded_by: null
ai_instructions:
  - "If a decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Keep the history intact: ADRs are architectural archaeology."
---

# ADR-XXX: <Short, declarative title>

> Example: "ADR-004: Synchronous Excel processing in the MVP"

## Status

`proposed` | `active` | `superseded by ADR-NNN` | `deprecated`

## Context

< What technical or business problem motivated this decision. Be specific.
Include: constraints, forces at play, what happens if we do NOT decide. >

Example:
> The PRD requires shops to upload an Excel file with their inventory. The ingestion doc proposes
> Celery + Redis + Pandas. For the MVP we must validate the model with < 10 shops and < 500 products,
> where processing takes < 1 s. The full async stack would add two weeks of work and infra cost
> before the product is validated.

## Options Considered

### Option A: <title>

**Description**: < how it would work >

**Pros**:
- ...

**Cons**:
- ...

**Cost**: < dev time + infra + maintenance complexity >

### Option B: <title>

(same structure)

### Option C: <title>

(same structure)

---

## Decision

**We choose Option <X>.**

< Justification in 1-2 paragraphs. Why this option beats the others IN THIS CONTEXT. >

---

## Consequences

### Positive

- ...
- ...

### Negative

- ...
- ...

### Accepted trade-offs

- ...

### Trigger to revisit

< Which metric or event should make us reopen this ADR. >

Example:
> - Upload latency above 5 s at p95
> - More than 100 concurrent shops
> - First timeout report on a large Excel file

---

## Implementation notes

- Affected files: `src/<package>/...`
- Tests: `tests/...`
- Related docs: `docs/project/mvp-scope.md` (row "..."), `docs/project/rules-registry.md`

---

## References

- `PRD.md` section X.Y
- `docs/architecture/<doc>.md`
- External link if applicable (paper, blog post, RFC)
