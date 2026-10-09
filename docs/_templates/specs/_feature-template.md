---
type: spec
feature: <feature-name>
status: draft  # draft | approved | in-progress | done | blocked
mvp: true     # is it in MVP scope? if not, it is not built now
prd_ref: section X.Y
depends_on:
  - architecture/data-model.md (entities X, Y)
  - architecture/api-contracts.md (D-Bus methods A, B)
verifiable_by:
  - tests/unit/<domain>/test_<feature>.py
  - tests/e2e/<domain>/test_<feature>_e2e.py
last_updated: YYYY-MM-DD
ai_instructions:
  - "This spec is a contract. Do NOT implement anything that is not in it."
  - "If you need to change the spec, update this doc FIRST and ask for confirmation."
  - "Every acceptance criterion must have a test that verifies it."
---

# Spec: <Feature Name>

## Goal (one sentence)

< What problem this feature solves for the user. >

Example: "Let the user rename a speaker and have every utterance of that speaker updated."

---

## Users / Personas

| Persona | How they use the feature |
|---|---|
| The developer (single user) | Reviews a processed meeting in the native review app |

---

## User stories

- **US-1**: As the user, I want to ..., so that ...
- **US-2**: As the user, I want to ..., so that ...

---

## Acceptance criteria (Gherkin to tests)

### AC-1: <title>

```gherkin
GIVEN a processed meeting with 3 detected speakers
WHEN I rename speaker 2 to "Alex"
THEN every utterance of speaker 2 shows "Alex"
AND meeting.json records the new name
```

**Test**: `tests/unit/speakers/test_rename.py::test_rename_updates_all_utterances`

### AC-2: <title>

(same structure)

---

## What this is NOT (out of scope for this spec)

- Not ...
- Not ...

---

## Edge cases

| Case | Expected behavior |
|---|---|
| Empty name | Rejected with `INVALID_VALUE` |
| Unknown speaker id | `ENTITY_NOT_FOUND_SPEAKER` |

---

## Definition of Done

- [ ] All acceptance criteria pass in `make check`
- [ ] D-Bus interface documented in `architecture/api-contracts.md`
- [ ] Unit tests written first (RED), then implementation (GREEN)
- [ ] Lint, format, type check and import contracts clean
- [ ] Traceability updated in `project/mvp-scope.md`
- [ ] New rules or gotchas added to `project/rules-registry.md`

---

## Decisions taken during implementation

> If unresolved forks appear, record them here AND open an ADR if the decision is structural.

- YYYY-MM-DD: <decision> — reason.
