---
type: runbook
status: active
last_updated: 2026-10-11
---

# Store the cloud API key in the GNOME Keyring

## Purpose

Give the service the OpenCode Go API key without writing it to any file (ADR-009, BR-005).

## Preconditions

- A GNOME session with the login keyring unlocked (`gnome-keyring`, `secret-tool` from `libsecret`).
- You have the key from your OpenCode Go subscription.

## Steps

1. Store the key. `secret-tool` reads the secret from standard input; never pass it as an argument:

   ```bash
   secret-tool store --label="Meeting Scribe OpenCode API key" application meeting-scribe key opencode-api-key
   ```

   Paste the key when prompted and press Enter.

2. Enable the cloud provider in your settings: `SUMMARIES_CLOUD_ENABLED=true` (and `SUMMARIES_PROVIDER=cloud` to make
   it the button's provider). The key is not a setting.

## Verify

```bash
secret-tool lookup application meeting-scribe key opencode-api-key >/dev/null && echo stored
```

This prints only `stored`, never the key.

## Rollback / troubleshooting

- Remove it: `secret-tool clear application meeting-scribe key opencode-api-key`.
- `SUMMARY_CLOUD_KEY_MISSING`: the key is not stored under those attributes, or the Secret Service is unreachable.
- `SUMMARY_CLOUD_KEY_LOCKED`: unlock the login keyring (log in again, or open Passwords and Keys) and retry. The
  service never prompts.
- Limitation: while your session is unlocked, any process running as you can read the key from the keyring.
