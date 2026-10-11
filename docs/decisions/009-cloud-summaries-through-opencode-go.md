---
type: decision
adr_number: 009
status: active  # proposed | active | superseded | deprecated
date: 2026-10-11
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "ADR-006 stays active; this ADR replaces only its cloud preset (DeepSeek direct). The port, the single adapter and the privacy rules are unchanged."
  - "Never put a real API key in code, tests, docs or logs. Tests use obvious fakes."
---

# ADR-009: Cloud summaries go through OpenCode Go

## Status

`active` - decided by the owner on 2026-10-11. It supersedes in part [ADR-006](006-summary-provider-openai-compatible.md):
only the "DeepSeek is a cloud preset" detail. ADR-006 is not edited.

## Context

ADR-006 names DeepSeek, called directly, as the cloud preset. The owner already pays for an OpenCode Go
subscription, an OpenAI-compatible gateway that serves several models (DeepSeek, GLM, Kimi, MiMo, LongCat) on
`/chat/completions` with a Bearer key. Paying twice for the same capability is pointless. The adapter is one
OpenAI-compatible class configured by `base_url`, `model` and `api_key`, so the change is configuration.
Safety requirement 3 (cloud is opt-in, labelled "cloud", confirmed on every request) is unaffected.

## Options Considered

### Option A: Keep DeepSeek direct

**Pros**: documented default, no change.
**Cons**: needs a second paid account and key the owner does not want.
**Cost**: none in code; recurring cost for the owner.

### Option B: OpenCode Go as the default cloud gateway

**Pros**: reuses the owner's subscription; one key, several models; same OpenAI-compatible client.
**Cons**: models served on `/responses` (Grok, GPT) or `/messages` (MiniMax) do not work with this adapter;
the gateway adds a third party between the service and the model vendor.
**Cost**: only settings defaults and docs.

---

## Decision

**We choose Option B.**

The cloud provider is an OpenAI-compatible gateway, default OpenCode Go. Defaults:
`SUMMARIES_CLOUD_BASE_URL=https://opencode.ai/zen/go/v1`, `SUMMARIES_CLOUD_MODEL=deepseek-v4-pro`,
`SUMMARIES_CLOUD_API_KEY` (a `SecretStr`, local configuration only). The owner may select any model the gateway
serves on `/chat/completions`. The "cloud" label is provider plus model, for example
"cloud: OpenCode Go, deepseek-v4-pro", and every cloud request still needs confirmation.

---

## Consequences

### Positive

- No second subscription; the model can change by editing one setting.

### Negative

- A gateway sits between the transcript and the model vendor; its privacy and retention terms apply.

### Accepted trade-offs

- Models that need `/responses` or `/messages` are unsupported until a revisit.

### Trigger to revisit

- The owner wants a model served only on `/responses` or `/messages`, or the gateway changes its endpoint.

---

## Implementation notes

- Affected: `SummariesSettings`, `.env.example`, `ARCHITECTURE.md` section 7, `docs/specs/summaries.md`.
- The key is sent only in the `Authorization` header and is never logged or stored in meeting files.

---

## References

- <https://opencode.ai/docs/go> (endpoint table), <https://opencode.ai/docs/zen>
- [ADR-006](006-summary-provider-openai-compatible.md)
