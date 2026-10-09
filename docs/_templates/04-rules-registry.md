---
type: registry
status: living  # ALWAYS 'living' — this doc is never "finished"
last_updated: YYYY-MM-DD
update_frequency: on-every-decision
ai_instructions:
  - "This is the PROJECT LOGBOOK. Rules accumulate, they are not replaced."
  - "BEFORE writing code, read the active rules of the category you are about to touch."
  - "AFTER making a decision (with or without the human), add the rule here BEFORE closing the session."
  - "Rules have an immutable ID (BR-XXX, CR-XXX). If a rule is invalidated, mark it 'deprecated' but do NOT delete it."
  - "Each rule needs: id, date, context, rule, source (PR/commit/conversation), status."
  - "If a new rule contradicts an active one, mark the old one 'superseded_by' and create the new one with the next ID."
---

# Living Rules Registry

> **This document is the project's institutional memory.** Every decision that affects how the
> project is built or how the product behaves lives here.
>
> It is updated **whenever a decision is made**, whether structural (ADR) or tactical (minor rule).

---

## How to use this registry

### When to add a rule

Add a rule when:
- A decision affects product behavior (business rule)
- A code convention is established that humans or AI must follow
- A gotcha is discovered that must be remembered
- Something is explicitly forbidden (anti-pattern detected)
- An implicit default is changed

Do NOT add a rule when:
- It is trivial and anyone would deduce it
- An ADR already covers it (link it, do not duplicate)
- It is a personal preference with no technical impact

### IDs and naming

- `BR-XXX` — **Business Rule**
- `CR-XXX` — **Code Rule** (technical convention)
- `GO-XXX` — **Gotcha** (discovery, edge case, "watch out")
- `AP-XXX` — **Anti-Pattern** (explicit prohibition)

IDs are **immutable**: once assigned they are never reused, even if the rule is deprecated.

### Status

- `active` — in force, must be respected
- `superseded` — replaced by another rule (link with `superseded_by`)
- `deprecated` — no longer applies, context changed

---

## Business Rules (BR)

### BR-001 — <short rule title>
- **Date**: YYYY-MM-DD
- **Status**: active
- **Context**: < why this rule exists >
- **Rule**: < the rule, stated verifiably >
- **Source**: PRD section / ADR / conversation
- **Verifiable in**: `src/<package>/...::<symbol>`
- **Test**: `tests/...::<test_name>`

---

## Code Rules (CR)

### CR-001 — <short rule title>
- **Date**: YYYY-MM-DD
- **Status**: active
- **Context**: < what went wrong or why this matters >
- **Rule**: < the convention >
- **Source**: < commit / conversation >
- **Verifiable in**: < file, lint rule or contract >

---

## Gotchas (GO)

### GO-001 — <short gotcha title>
- **Date**: YYYY-MM-DD
- **Status**: active
- **Symptom**: < what you observe >
- **Cause**: < root cause >
- **Solution**: < what to do >

---

## Anti-Patterns (AP)

### AP-001 — <short prohibition>
- **Date**: YYYY-MM-DD
- **Status**: active
- **Reason**: < why it is forbidden >
- **Source**: < ADR / conversation >

---

## Statistics (keep updated)

| Category | Active | Superseded | Deprecated |
|---|---|---|---|
| BR | 0 | 0 | 0 |
| CR | 0 | 0 | 0 |
| GO | 0 | 0 | 0 |
| AP | 0 | 0 | 0 |

**Last rule added**: —
**Next available IDs**: BR-002, CR-002, GO-002, AP-002

---

## How to deprecate or supersede a rule

```markdown
### BR-001 — Old rule title
- **Date**: 2026-04-28
- **Status**: superseded
- **Superseded by**: BR-007 (2026-06-15)
- **Reason for the change**: user feedback, the old limit was too low
- (the rest of the content stays as history)

### BR-007 — New rule title
- **Date**: 2026-06-15
- **Status**: active
- **Supersedes**: BR-001
- ...
```

**Golden rule**: never delete rules. The archaeology explains why things are the way they are.
