---
type: decision
adr_number: 006
status: active  # proposed | active | superseded | deprecated
date: 2026-10-09
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Cloud providers are opt-in, labelled 'cloud' in the review app, and confirmed per request. API keys come only from env/.env and are never logged."
  - "The summary structure follows the reference format below; use placeholders in tests and docs, never real meeting content."
---

# ADR-006: LLM summaries through a `SummaryProvider` port with an OpenAI-compatible adapter

## Status

`active` — approved by the user in the F0 review (2026-10-09).

## Context

After review, the user wants a meeting summary: topics, decisions, action items and open questions. The default
must be fully local (Ollama). The user also wants the option to use a stronger cloud model (DeepSeek). Sending a
transcript to a cloud provider is the only way meeting content can leave the machine, so it must be explicit
(BR-003, BR-004). Ollama and DeepSeek both expose OpenAI-compatible chat endpoints.

## Options Considered

### Option A: Provider-specific SDKs per vendor

**Description**: one adapter per vendor using that vendor's SDK.

**Pros**:
- Access to vendor-specific features.

**Cons**:
- An adapter, dependency and test set per provider; more code for the same capability.
- Switching or adding a provider means new code.

**Cost**: grows linearly with providers.

### Option B: Local-only (Ollama)

**Description**: support only a local model.

**Pros**:
- No data leaves the machine, ever.
- Simplest privacy story.

**Cons**:
- The user explicitly wants the option of a stronger cloud model.
- Quality limited by what fits the 8 GB GPU once Whisper is released.

**Cost**: lowest, but does not meet the requirement.

### Option C: `SummaryProvider` port with one OpenAI-compatible adapter

**Description**: a port in `summaries/domain`; one adapter configured by `base_url`, `model` and `api_key`.
Ollama (`http://127.0.0.1:11434/v1`) is the default; DeepSeek is a cloud preset.

**Pros**:
- One adapter covers local and cloud and any future OpenAI-compatible provider.
- Switching provider is configuration.
- Easy to fake behind the port in tests.

**Cons**:
- Limited to what the OpenAI-compatible surface offers.
- Provider quirks (token limits, model names) still need handling.

**Cost**: low.

---

## Decision

**We choose Option C.**

One thin adapter satisfies both the local default and the cloud option, and the port keeps the domain independent
of any vendor. Privacy rules are enforced by configuration and review-app flow, not by hoping the user remembers.

---

## Consequences

### Positive

- Local by default; cloud only by deliberate choice.
- A single code path to test and maintain.
- New compatible providers need no code.

### Negative

- Vendor-specific features are out of reach without a new adapter.
- Long transcripts may exceed a model's context; chunking or map-reduce summarization is needed.

### Accepted trade-offs

- Convenience of vendor SDKs is traded for one uniform adapter.

### Trigger to revisit

- A required provider is not OpenAI-compatible.
- A feature needs vendor-specific capabilities (for example structured output only one vendor offers).

---

## Implementation notes

- **API key, cloud opt-in and the local adapter (2026-10-12)**: three details of this ADR changed during F5 and are superseded in part by [ADR-009](009-cloud-summaries-through-opencode-go.md); the decision text above is kept as the record. (1) The API key is not read from env/`.env`: it lives in the GNOME Keyring and is read only when a confirmed cloud summary starts. (2) The `cloud_enabled` flag was removed: choosing `provider=cloud` in the Preferences window (or `SUMMARIES_PROVIDER=cloud` as a developer override) is the opt-in; the cloud label and the per-request confirmation are unchanged ([preferences spec](../specs/preferences.md) section 7). (3) Local runs use the native Ollama API, as the next note describes.

- **Local provider is native Ollama (2026-10-12, owner decision after measuring)**: Ollama's OpenAI-compatible endpoint cannot set the context size per request (it stays at 4096), so long meetings split into many slow chunks. The local adapter, `OllamaChatProvider`, therefore calls `POST <origin>/api/chat` (`options.num_ctx`, `options.num_predict`, `think: false`, streaming NDJSON) behind the same `SummaryProvider` port; the OpenAI-compatible adapter stays for the cloud. The decision to have one port stands; the "one adapter covers local and cloud" consequence no longer does. See the summaries spec section 5 and rules-registry GO-064.

- Port: `summaries/domain` (`SummaryProvider`: transcript and options in, summary out, returning `Result`). Adapter: `summaries/infrastructure`, using `base_url`, `model`, `api_key`.
- Settings: a `SummariesSettings` class with `env_prefix` (for example `SUMMARIES_`), composed into `Settings`; provider choice, base URL, model, and an explicit `cloud_enabled` flag default to local and disabled.
- **Privacy rules**: cloud must be explicitly enabled in settings, visibly labelled "cloud" in the review app (ADR-005), and **confirmed per request**. The API key is read only from env/`.env`, never written to meeting folders and never logged. Local Ollama requests never leave loopback.
- **Output format** (in the meeting's language; placeholders shown, never real content):

```
# <Meeting title> — <date> (<time>, ~<N> min)
**Participants:** <name>, <name>, ...
> Note: the transcript may contain recognition errors in names and technical terms.

## Topics
### 1. <topic>
- <point>
### 2. <topic>
- <point>

## Action items
| Owner | Task |
|---|---|
| <name> | <task> |

## Open questions
- <question>
```

- Language follows the meeting language (detected by Whisper) unless a setting overrides it.
- Handle long transcripts by chunked summarization; tests use fake providers and synthetic transcripts.
- Docs to update when built: `docs/architecture/api-contracts.md`, `docs/specs/` for the summary feature, `.env.example`, `ARCHITECTURE.md` section 7.

---

## References

- `PRD.md` FR-6, safety requirement 3, open questions 2 and 5
- [`../project/rules-registry.md`](../project/rules-registry.md): BR-003, BR-004
- [ADR-005](005-review-ui-native-gtk-app.md)
