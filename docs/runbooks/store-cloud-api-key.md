---
type: runbook
status: active
last_updated: 2026-10-12
---

# Store the cloud API key in the GNOME Keyring

## Purpose

Give the service the OpenCode Go API key without writing it to any file (ADR-009, BR-005).

## Preconditions

- A GNOME session with the login keyring unlocked (`gnome-keyring`, `secret-tool` from `libsecret`).
- You have the key from your OpenCode Go subscription.

## Steps

**Recommended: the Preferences window.** Open Preferences from the extension menu, choose **In the cloud**, press
**Set API key...** and paste the key. The window stores it in the GNOME Keyring and never shows it again. Picking the
cloud provider there is the whole opt-in ([`preferences.md`](preferences.md)). The steps below are the developer
override.

1. Store the key. `secret-tool` reads the secret from standard input; never pass it as an argument:

   ```bash
   secret-tool store --label="Meeting Scribe OpenCode API key" application meeting-scribe key opencode-api-key
   ```

   Paste the key when prompted and press Enter.

2. Choose the cloud provider in your settings: `SUMMARIES_PROVIDER=cloud` (choosing it is the whole opt-in; there is no
   separate switch). The key is not a setting.

## Verify

```bash
secret-tool lookup application meeting-scribe key opencode-api-key >/dev/null && echo stored
```

This prints only `stored`, never the key.

## Rollback / troubleshooting

- Remove it: `secret-tool clear application meeting-scribe key opencode-api-key`.
- `SUMMARY_CLOUD_KEY_MISSING`: the key is not stored under those attributes.
- `SUMMARY_CLOUD_KEY_UNAVAILABLE`: the Secret Service did not answer (gnome-keyring not running, no session bus, or a timeout); check that `gnome-keyring` is running.
- `SUMMARY_CLOUD_KEY_LOCKED`: unlock the login keyring (log in again, or open Passwords and Keys) and retry. The
  service never prompts.
- Limitation: while your session is unlocked, any process running as you can read the key from the keyring.
