---
type: runbook
status: active
last_updated: 2026-10-12
phase: F5
verifiable_by:
  - scripts/install.sh
  - tests/unit/test_install_scripts.py
  - .env.example
ai_instructions:
  - "An agent must NEVER run these steps against the owner's machine: they talk to Ollama, to the cloud gateway and to the keyring, and summaries are built from private transcripts. Only the user runs them."
  - "Never pull a model, start Ollama or send a prompt on the user's behalf. The preflight and `./install.sh` only check."
  - "Never put an API key, a transcript or a summary in the repo, a log or a doc."
---

# Set up and troubleshoot summaries

## Purpose

Run the "Generate summary" button of the review app (F5) with a local model through Ollama, optionally with the
cloud gateway OpenCode Go, and fix the usual problems. Design: [`../specs/summaries.md`](../specs/summaries.md);
the cloud choice: [ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md).

## Preconditions

- `./install.sh` (alias `make install`) was run (service, review app and extension). Its preflight already required Ollama and the model, and its summary has an `ollama:` line.
- Settings are changed in the **Preferences** window (Meeting Scribe menu, or the gear in the Extensions app); see
  [`preferences.md`](preferences.md). Model and provider changes apply at once, with no restart. Developers can still
  override any of them with `SUMMARIES_*` variables in the environment or in `~/.config/meeting-scribe/.env`
  (template: [`.env.example`](../../.env.example)); that override wins over Preferences and needs a service restart:
  `systemctl --user restart meeting-scribe.service`.
- A meeting in the review app that is processed (state done). The summary is built from its transcript.

## Steps: local summaries with Ollama

The preflight (`./install.sh --check`, rows `ollama.installed`, `ollama.reachable`, `ollama.model`) **checks** Ollama and
the model, read-only (one GET of `/api/tags` on `SUMMARIES_LOCAL_BASE_URL`, default `http://127.0.0.1:11434`). It never
starts Ollama, never pulls a model and never sends a prompt; a gap stops the install with zero changes and prints the
command to fix it (exit 10). Run the command, then `./install.sh --check` again.

1. Install Ollama from <https://ollama.com/download> and make sure it runs. On a system install this is
   `systemctl status ollama`; start it with `sudo systemctl start ollama` (or run `ollama serve` in a terminal).
2. Pull the default model (about 2.5 GB; it is a download, so run it yourself):

   ```bash
   ollama pull qwen3:4b-instruct-2507-q4_K_M
   ```

3. Check that the service sees it: `ollama list` shows `qwen3:4b-instruct-2507-q4_K_M`. The install summary
   then reads `ollama:    reachable at preflight; model qwen3:4b-instruct-2507-q4_K_M present`.
4. Open a processed meeting in the review app, go to the Summary tab and press **Generate summary**. A local summary
   sends the transcript to the local model only; nothing leaves the machine.

### Model choice

The default is `qwen3:4b-instruct-2507-q4_K_M` (2.5 GB, text-only, non-thinking, Apache-2.0). It was chosen by
measurement on an 8 GB RTX 5060: it runs **100% on the GPU at a 16k context** (peak about 5 GB), so a 2-hour meeting
(about 10k tokens) is one pass. Larger models did not fit: `qwen3.5:9b` loads at 8.8 GB even at 4k context (about 27%
of it on the CPU, slow, and it failed at 16k) and `granite4.1:8b` took 35 minutes at 16k with 23% on the CPU.

In Preferences the model is picked from the models `ollama list` shows (developer override:
`SUMMARIES_LOCAL_MODEL`); the install check follows the same precedence (environment, `.env`, then the Preferences
file). A model that does not fit the GPU makes Ollama split it between GPU and CPU, and the summary becomes very
slow. **Check the split with `ollama ps` while a summary runs**: the `PROCESSOR` column must read `100% GPU`.

### Context size

The service talks to Ollama's native API (`/api/chat`) and sends the context size with every request
(`options.num_ctx` = `SUMMARIES_LOCAL_CONTEXT_TOKENS`, default `16384`), so nothing has to be configured in Ollama
(no `OLLAMA_CONTEXT_LENGTH`, no Modelfile). The native root is derived from `SUMMARIES_LOCAL_BASE_URL`: the scheme and
host of `http://127.0.0.1:11434/v1` give `http://127.0.0.1:11434/api/chat`; the host must stay loopback.

Output is bounded too: `SUMMARIES_NOTES_MAX_OUTPUT_TOKENS` (default `768`) for the notes of long meetings and
`SUMMARIES_MAX_OUTPUT_TOKENS` (default `3072`) for the final summary. The prompts ask for short notes (at most 12
bullets per part) and a capped summary (at most 8 topics, at most 15 task rows), because a token cap only cuts an answer.

A bigger context needs more VRAM. If the answer is cut or the context is too large, the app shows the error with the
setting to change (see Troubleshooting).

## Steps: cloud summaries through OpenCode Go (opt-in)

The cloud is off by default. A cloud summary sends the transcript, with speaker names, to the gateway
([ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md)); it happens only when you confirm it, every time.

1. Store the API key in the GNOME Keyring, never in a file (BR-005):
   normally with **Set API key...** in Preferences (it writes to the keyring entry; the Summary tab only points to
   Preferences when the key is missing); the manual alternative is [`store-cloud-api-key.md`](store-cloud-api-key.md).
2. In Preferences choose **Run summaries: In the cloud**, then pick the Model from the list (the list needs the key).
   Developer override: `SUMMARIES_PROVIDER=cloud`. That is the whole opt-in; the caption under the button says "Cloud" and the button asks for confirmation before it sends anything.
   `SUMMARIES_CLOUD_ENABLED` no longer exists. Upgrading never switches the cloud on: a `SUMMARIES_PROVIDER=cloud` line in
   the environment or `.env` without `SUMMARIES_CLOUD_ENABLED=true` next to it (or with it `false`) keeps the provider
   local, logs one warning and locks the field in Preferences until you delete those lines; then choose Cloud in the
   window. `SUMMARIES_CLOUD_ENABLED=true` with the provider `local` no longer allows a per-request cloud summary (one
   button, one provider); it is ignored with a warning.
   `SUMMARIES_CLOUD_MODEL` accepts any model the gateway serves on `/chat/completions` (default `deepseek-v4-pro`).
3. Press **Generate summary** (no restart is needed after a Preferences change); read the confirmation dialog and confirm only if you accept
   that the transcript leaves this computer.

## Verify

```bash
ollama list                      # the configured model is listed
ollama ps                        # while a summary runs: model, CONTEXT, GPU/CPU split
systemctl --user status meeting-scribe.service
```

A finished summary appears in the Summary tab and as `summary.md` next to the meeting. The panel menu then shows
**Open last summary (date)**, which opens that file in the default app for Markdown files.

## Rollback / troubleshooting

| Symptom or code | Cause and fix |
|---|---|
| `[ FIX ] ollama.installed` | Ollama is not installed or not on `PATH`. Install it from <https://ollama.com/download>, then `ollama pull qwen3:4b-instruct-2507-q4_K_M`. |
| `[ FIX ] ollama.reachable` | The server is stopped. `sudo systemctl start ollama` or `ollama serve`, then `./install.sh --check` again. |
| `[ FIX ] ollama.model` | `ollama pull <model>` (the command is in the report). The install never pulls. |
| `SUMMARY_PROVIDER_UNAVAILABLE` | Ollama is unreachable from the service. Start it; `SUMMARIES_LOCAL_BASE_URL` must point at it. |
| `SUMMARY_MODEL_RUN_FAILED` | Ollama is up but could not run the model (load failure or out of memory). Check `ollama ps` and `journalctl -u ollama`, free GPU memory or pick a smaller model. It is not retried. |
| `SUMMARY_MODEL_NOT_FOUND` | The model is not in `ollama list`: pull it, or pick another one in Preferences (or fix `SUMMARIES_LOCAL_MODEL` if you override it). |
| `SUMMARY_CONTEXT_TOO_LARGE` | The prompt reached the context window (Ollama would drop its start). Raise `SUMMARIES_LOCAL_CONTEXT_TOKENS` if the GPU still holds the model 100% (check `ollama ps`), or pick a smaller model. |
| `SUMMARY_OUTPUT_LIMIT_REACHED` | The model used its whole output limit. Raise `SUMMARIES_MAX_OUTPUT_TOKENS` (final summary) or `SUMMARIES_NOTES_MAX_OUTPUT_TOKENS` (notes steps), or choose another model. |
| `SUMMARY_PROVIDER_REJECTED` (HTTP 400) from the cloud | OpenCode Go rejects requests without client and session identification (`MissingSessionID`). The service sends `User-Agent: meeting-scribe/<version>` and `x-opencode-session` itself; a `SUMMARIES_CLOUD_BASE_URL` override that points at another gateway does not change that (GO-063). |
| `SUMMARY_TIMEOUT` | The provider sent no data in time; retry, or raise `SUMMARIES_TIMEOUT_SECONDS`. A model that spills to the CPU is slow. |
| `SUMMARY_GPU_BUSY`, `SUMMARY_BUSY` | A recording, processing or another summary holds the GPU. Wait; processing resumes by itself. |
| `SUMMARY_CLOUD_DISABLED` | The provider is not `cloud`: choose **In the cloud** in Preferences (developer override: `SUMMARIES_PROVIDER=cloud`). |
| `SUMMARY_CLOUD_KEY_MISSING`, `..._LOCKED`, `..._UNAVAILABLE` | Store, unlock or reach the keyring: see [`store-cloud-api-key.md`](store-cloud-api-key.md). |
| `SUMMARY_AUTH_FAILED`, `SUMMARY_RATE_LIMITED` | The gateway refused the key, or the plan limit is reached. Check the subscription and store the key again. |
| "Open last summary" does nothing | No app is set for Markdown files. `xdg-mime default <app>.desktop text/markdown`. |

Codes are listed in [`../architecture/error-codes-catalog.md`](../architecture/error-codes-catalog.md). To go back to
no summaries at all, leave the cloud off and stop Ollama; recording and processing never need either. `./uninstall.sh`
leaves meetings, summaries, the settings file, the keyring items and the downloaded models untouched.
