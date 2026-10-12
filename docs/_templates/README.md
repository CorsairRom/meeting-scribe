# Documentation Templates for AI-First Projects

> Ready-to-copy templates for starting a project with an AI agent as the main builder.
> Adapted (and translated to English) from an earlier internal project's template set.

## Why these templates

Typical project docs (PRD, ADR, specs) are written for humans: narrative prose, tolerated ambiguity.
An AI agent needs something else: verifiable contracts, explicit scope, measurable "done" criteria.

These templates fix the gaps that appear without them:

1. **Undefined MVP scope** — the agent silently cuts or invents features.
2. **Incomplete schema** — the agent invents field names.
3. **Missing API contracts** — client and server drift apart.
4. **No orchestration guidance** — the agent works serially when it could parallelize.
5. **No living rules registry** — decisions get lost and gotchas are rediscovered.
6. **No sync protocol** — docs and code diverge silently.

## Recommended adoption order

| Step | Doc | When |
|---|---|---|
| 1 | Root `PRD.md` | Day 0 — product vision. There is no PRD template: the root `PRD.md` of this repo is the model. |
| 2 | Root `ARCHITECTURE.md` | Day 0 — stack and hard constraints. Also modelled on this repo's own file. |
| 3 | `02-mvp-scope.md` | Day 0 — **do not skip**, defines what is built now |
| 4 | `03-orchestration.md` | Day 0 — how to parallelize the AI's work |
| 5 | `04-rules-registry.md` | Day 0 empty — **fills itself during development** |
| 6 | `05-doc-sync-protocol.md` | Day 0 — mandatory update protocol |
| 7 | `architecture/data-model.md` | Before touching persistence |
| 8 | `architecture/api-contracts.md` | Before touching any client |
| 9 | `specs/_feature-template.md` | One per feature, before implementing |
| 10 | `decisions/_adr-template.md` | When a fork has lasting consequences |

## Cross-cutting principles

- **Actionable frontmatter**: `type`, `status`, `last_updated`, `ai_instructions`, and where useful `depends_on`, `verifiable_by`.
- **Acceptance criteria in Gherkin** (Given/When/Then), convertible to tests.
- **Tables over prose** for contracts.
- **Inline Mermaid** for diagrams (agents read them well).
- **An explicit "What this is NOT" section.**
- **Real examples**, not `foo/bar` (synthetic data only where privacy requires it).

## Included templates

| File | Purpose |
|---|---|
| `02-mvp-scope.md` | The scope contract: what is in, out next, out indefinitely |
| `03-orchestration.md` | How to direct an AI and parallel subagents |
| `04-rules-registry.md` | **Living** registry of business and code rules (BR/CR/GO/AP) |
| `05-doc-sync-protocol.md` | **Mandatory** docs-to-code sync protocol |
| `architecture/data-model.md` | Complete data model, each field justified |
| `architecture/api-contracts.md` | Endpoint-by-endpoint contracts |
| `specs/_feature-template.md` | Base for feature specs |
| `decisions/_adr-template.md` | Base for ADRs |

## The most underrated doc: `04-rules-registry.md`

It is the only doc that is never finished. It grows every day and collects:

- Business decisions too small for a full ADR (BR-NNN)
- Code conventions established along the way (CR-NNN)
- Gotchas discovered during development (GO-NNN)
- Forbidden anti-patterns (AP-NNN)

Without it, the AI relearns the same lessons every session. With it, context loads in 30 seconds.

## The doc that keeps the rest from rotting: `05-doc-sync-protocol.md`

It defines the **mandatory update triggers**: change a model, update the data model in the same commit;
make a decision, add a rule to the registry. No exceptions. Without a protocol, docs become archaeology within three months.
