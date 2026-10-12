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
the API key is **not a setting**: it lives in the GNOME Keyring (see below). The owner may select any model the gateway
serves on `/chat/completions`. The "cloud" label is provider plus model, for example
"cloud: OpenCode Go, deepseek-v4-pro", and every cloud request still needs confirmation.

### Where the API key lives (owner decision, 2026-10-11)

The key is stored in the GNOME Keyring (freedesktop Secret Service, `org.freedesktop.secrets`), not in `.env` or any
settings file. The service reads it on demand over the session bus with `dbus-fast` (no new dependency), only when a
confirmed cloud summary starts, behind a `CloudKeyStore` port, and holds it in memory (`SecretStr`) for that request
only. Lookup attributes: `application=meeting-scribe`, `key=opencode-api-key`; label "Meeting Scribe OpenCode API key".
The "plain" Secret Service session is used: the transfer is a local Unix socket of the user's own session bus, so
there is no network exposure, and the "dh-ietf1024" algorithm would add a weak 1024-bit exchange and a cipher
dependency for no gain on a local socket. A locked keyring or a missing key fails with `SUMMARY_CLOUD_KEY_LOCKED` or
`SUMMARY_CLOUD_KEY_MISSING`; the service never prompts and never calls `Unlock`.

Invariants: the key is never logged, never in an error message, `meeting.json`, a D-Bus reply, a command line or a
worker environment; it is sent only over HTTPS to the configured base URL; it is held in memory only for the request.
Honest limitation: while the session is unlocked, any process running as the user can query the keyring (there is no
per-application ACL outside Flatpak). The key is stored with
`secret-tool store --label="Meeting Scribe OpenCode API key" application meeting-scribe key opencode-api-key`
(secret on stdin); see `docs/runbooks/store-cloud-api-key.md`. A "Set API key" field in the review app is F5.6 scope.

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
- The key is sent only in the `Authorization` header and is never logged or stored in meeting files or settings files.
- **Local provider (2026-10-12)**: the OpenAI-compatible adapter is now used only for the cloud; the local provider is the native Ollama adapter (ADR-006 implementation note). Nothing about the cloud gateway changed.
- **Session identification (2026-10-12, F5.9)**: OpenCode Go rejects requests without it (HTTP 400 `MissingSessionID`). Every cloud request carries `User-Agent: meeting-scribe/<version>`, and every chat request carries `x-opencode-session` with an opaque per-job id (a hash of a random UUID, never derived from the meeting). The requirement is a field of the provider entry in `CLOUD_PROVIDERS`. See rules-registry GO-063 and <https://opencode.ai/docs/go>.

---

## References

- <https://opencode.ai/docs/go> (endpoint table), <https://opencode.ai/docs/zen>
- [ADR-006](006-summary-provider-openai-compatible.md)
