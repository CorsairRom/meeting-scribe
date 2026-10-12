---
type: index
status: living
last_updated: 2026-10-12
---

# Specs

**Question answered here**: what exactly must this feature do?

A spec is the behavioral contract for one feature: goal, user stories, Gherkin acceptance criteria
(each backed by a test), edge cases, what the feature is NOT, and a Definition of Done.

- **Naming**: `kebab-case-feature.md` (for example `speaker-merge.md`), one file per feature.
- **Template**: copy [`../_templates/specs/_feature-template.md`](../_templates/specs/_feature-template.md).
- **Rule**: write the spec before the code of a feature that has behavioral nuance; update it first when behavior changes.
- **Not here**: why a decision was taken (`decisions/`), how the system is built (`architecture/`).

| Spec | Phase | Status |
|---|---|---|
| [`processing.md`](processing.md) | F3 | done |
| [`review-app.md`](review-app.md) | F4 | done |
| [`summaries.md`](summaries.md) | F5 | in-progress |
| [`preferences.md`](preferences.md) | F5 (F5.10) | in-progress |

The "Me" versus system labelling algorithm (ADR-002) is specified in `processing.md` section 2.4.
