---
type: architecture
status: draft
last_updated: YYYY-MM-DD
mvp: true
verifiable_by:
  - src/<package>/*/domain/*.py
ai_instructions:
  - "This doc is the SOURCE OF TRUTH for the data model. If a model changes, update THIS file in the same commit."
  - "Every field has a justification. Do not add fields without justifying them here first."
  - "A field marked [next] is NOT implemented in the MVP."
---

# Data Model

**Storage**: < engine or format >
**Access**: < repository ports and adapters >

---

## Entity-relationship diagram

```mermaid
erDiagram
  Meeting ||--o{ Speaker : "1:N"
  Meeting ||--o{ Utterance : "1:N"
  Speaker ||--o{ Utterance : "1:N"

  Meeting { string id PK; string status; datetime started_at }
  Speaker { string id PK; string meeting_id FK; string name }
  Utterance { string id PK; string speaker_id FK; float start; float end; string text }
```

---

## Global conventions

- **IDs**: < format and why >
- **Timestamps**: < format >
- **Schema versioning**: < how persisted documents are versioned >
- **State fields**: < enum strategy >

---

## Entities

### `<Entity>`

| Field | Type | Constraint | Justification |
|---|---|---|---|
| `id` | string | PK | Convention |
| `name` | string | not empty | Display |

**Invariants**: < rules the entity itself guarantees >
**Transitions**: < state transitions as entity methods >

---

## Data NOT modelled (explicit decisions)

| Entity | Reason |
|---|---|
| `User` | Single-user tool |

---

## Migrations / schema evolution

< How persisted data is upgraded when the schema changes. >
