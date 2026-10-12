---
type: decision
adr_number: 011
status: proposed  # proposed | active | superseded | deprecated
date: 2026-10-12
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Draft: it becomes active when the owner approves docs/specs/install.md."
  - "The Hugging Face token is never an argument, never in a D-Bus message, never logged and never a setting."
---

# ADR-011: The Hugging Face token lives in the keyring and models are downloaded from Preferences

## Status

`proposed` - drafted with [`../specs/install.md`](../specs/install.md) on 2026-10-12, from the owner's decisions of the same day. It follows [ADR-009](009-cloud-summaries-through-opencode-go.md) (a secret lives in the GNOME Keyring) and refines the install-time model fetch of the F3 spec (rules-registry GO-047).

## Context

The diarization model is gated, so a download needs an accepted licence and a token. Today the install downloads the
models and relies on a prior `hf auth login`; processing then loads with `token=True`, which raises
`LocalTokenNotFoundError` without a stored login even when the models are cached and the worker is offline.
The user should not need a console, and a missing token must not stop the install: recording needs no model.

## Options Considered

### Option A: Keep `hf auth login` and the install-time download

**Pros**: no new code.
**Cons**: a console step, a failed install summary for a missing token, a load that depends on a login it never needs.
**Cost**: none.

### Option B: Token in `.env` or settings

**Pros**: simple.
**Cons**: a secret in a plain file; breaks the rule set for the cloud key.
**Cost**: low, but unsafe.

### Option C: Token in the GNOME Keyring, service-side download triggered from Preferences

**Pros**: same pattern as the cloud key; the token crosses no D-Bus message; clients stay thin; processing needs no token.
**Cons**: a new D-Bus interface and a download runner.
**Cost**: a `model_setup` domain, `Models1`, a Preferences group.

---

## Decision

**We choose Option C.**

The token is stored by the review app in the GNOME Keyring (`application=meeting-scribe`, `key=huggingface-token`) and
read on demand by the service. `Models1.Download` runs `prefetch-models` as a child process and writes the token to its
standard input only (never argv, environment, logs or D-Bus). Processing loads the cache with `token=None`. The keyring
token wins over the ambient login; with no keyring token the ambient login is used, so `hf auth login` keeps working.
The installer never stops for a missing token: it reports "models pending". A locked keyring is an error; nothing prompts.

---

## Consequences

### Positive

- No console step; recording works right after the install; a cached model loads with no login.

### Negative

- A new interface (`dev.meetingscribe.Models1`) and a new domain to maintain.

### Accepted trade-offs

- While the session is unlocked, any process of the user can query the keyring (the limitation ADR-009 already accepts).

### Trigger to revisit

- A packaged distribution that ships the models, or a Hugging Face change to gating.

---

## Implementation notes

- Affected: `src/meeting_scribe/model_setup/`, `pyannote_backend.py`, `prefetch.py`, `cli.py`, `review-app/` (Preferences), `docs/architecture/api-contracts.md`, `docs/architecture/error-codes-catalog.md`.
- Tests: `tests/unit/model_setup/`, `tests/e2e/model_setup/`, `tests/unit/speakers/test_pyannote_backend.py`, `tests/unit/review_app/`.

---

## References

- [`../specs/install.md`](../specs/install.md) sections 6 to 8
- [ADR-009](009-cloud-summaries-through-opencode-go.md)
