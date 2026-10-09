---
type: scope
status: draft
last_updated: YYYY-MM-DD
ai_instructions:
  - "This is the contract of what IS and IS NOT implemented in the current release."
  - "If a feature is not in 'IN SCOPE', do NOT build it now even if it appears in the PRD."
  - "When you complete a feature, update its status and add the date and commit."
  - "If the PRD and this doc conflict, THIS doc wins for the current release."
---

# MVP Scope — Release 0

**MVP goal (one sentence)**: < e.g. Validate that a user can find a product in a nearby shop and contact it in < 60 s. >

**Definition of MVP Done**: < e.g. 10 real shops onboarded, 100 real searches, 5 conversions. >

**MVP non-goals**: < e.g. monetization, payments, mobile app, multi-country. >

---

## IN SCOPE — MVP (Release 0)

| Feature | PRD ref | Acceptance | Owner | Status |
|---|---|---|---|---|
| Geo search by product + area | §3.2 | `specs/search.md` | backend+frontend | in progress |
| Server-rendered landing page | §3.1 | `specs/seo-landing.md` | frontend | done 2026-04-29 a1b2c3d |
| Synchronous Excel upload (no queue) | §3.3 | `specs/ingestion.md` | backend | done |

**Status**: in progress · done · blocked · paused

---

## OUT OF SCOPE — next release

> These features are in the PRD but are NOT implemented now. If an AI builds them unasked, it is out of scope.

| Feature | PRD ref | Reason to postpone | Trigger to activate |
|---|---|---|---|
| Async Excel with a task queue | `ingestion-pipeline.md` | Sync is enough for < 1000 rows | > 5 s latency or > 100 concurrent shops |

---

## OUT OF SCOPE — indefinitely

| Feature | Why never (or not soon) |
|---|---|
| Native mobile app | A PWA covers the use case |

---

## Traceability: PRD → Scope → Code → Test

| PRD § | Feature | Doc | Test | Code |
|---|---|---|---|---|
| 3.1 | Aggregator landing | `specs/seo-landing.md` | `tests/test_seo_api.py` | `apps/search/seo_api.py` |

> When you add a feature, fill its row. If a column is empty, that feature is not finished.
