---
type: runbook
status: active
last_updated: 2026-10-11
phase: F5
verifiable_by:
  - scripts/install.sh
  - tests/unit/test_install_scripts.py
  - .env.example
ai_instructions:
  - "An agent must NEVER run these steps against the owner's machine: they talk to Ollama, to the cloud gateway and to the keyring, and summaries are built from private transcripts. Only the user runs them."
  - "Never pull a model, start Ollama or send a prompt on the user's behalf. `make install` only checks."
  - "Never put an API key, a transcript or a summary in the repo, a log or a doc."
---

# Set up and troubleshoot summaries

## Purpose

Run the "Generate summary" button of the review app (F5) with a local model through Ollama, optionally with the
cloud gateway OpenCode Go, and fix the usual problems. Design: [`../specs/summaries.md`](../specs/summaries.md);
the cloud choice: [ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md).

## Preconditions

- `make install` was run (service, review app and extension). Its summary has an `ollama:` line.
- Settings live in `~/.config/meeting-scribe/.env` (template: [`.env.example`](../../.env.example)). Restart the
  service after editing them: `systemctl --user restart meeting-scribe.service`.
- A meeting in the review app that is processed (state done). The summary is built from its transcript.

## Steps: local summaries with Ollama

`make install` only **checks** Ollama and the model, read-only (`ollama list`). It never starts Ollama, never pulls a
model and never sends a prompt; a gap is a warning, the install still succeeds. Re-run `make install` to re-check.

1. Install Ollama from <https://ollama.com/download> and make sure it runs. On a system install this is
   `systemctl status ollama`; start it with `sudo systemctl start ollama` (or run `ollama serve` in a terminal).
2. Pull the default model (about 6.6 GB; it is a download, so run it yourself):

   ```bash
   ollama pull qwen3.5:9b
   ```

3. Check that the service sees it: `ollama list` shows `qwen3.5:9b`. The install summary then reads
   `ollama:    reachable; model qwen3.5:9b present`.
4. Open a processed meeting in the review app, go to the Summary tab and press **Generate summary**. A local summary
   sends the transcript to the local model only; nothing leaves the machine.

### Model choice

`SUMMARIES_LOCAL_MODEL` is any model name `ollama list` shows (default `qwen3.5:9b`, which fits an 8 GB GPU with a
short context). Set `SUMMARIES_LOCAL_MODEL` in the settings file and the install check follows it. A larger model
or a longer context may not fit the GPU; Ollama then splits the model between GPU and CPU and the summary is slower.
Check the split with `ollama ps` while a summary runs.

### Context size

Ollama's own default context is 4096 tokens below 24 GiB of VRAM (check the `CONTEXT` column of `ollama ps`), and the
OpenAI-compatible endpoint the service uses cannot change it per request. A long meeting is summarized in steps that
fit `SUMMARIES_LOCAL_CONTEXT_TOKENS` (default `4096`), so the two numbers must match. Raise both together, in one of two
ways:

- For the whole Ollama server: set `OLLAMA_CONTEXT_LENGTH` (for a system install, `sudo systemctl edit ollama` and add
  `Environment="OLLAMA_CONTEXT_LENGTH=8192"`, then `sudo systemctl restart ollama`).
- For one model only, with a Modelfile:

  ```bash
  printf 'FROM qwen3.5:9b\nPARAMETER num_ctx 8192\n' > Modelfile
  ollama create qwen3.5-9b-8k -f Modelfile
  ```

  then set `SUMMARIES_LOCAL_MODEL=qwen3.5-9b-8k` and `SUMMARIES_LOCAL_CONTEXT_TOKENS=8192`.

A bigger context needs more VRAM. If the answer is cut or the context is too large, the app shows the error with the
setting to change (see Troubleshooting).

## Steps: cloud summaries through OpenCode Go (opt-in)

The cloud is off by default. A cloud summary sends the transcript, with speaker names, to the gateway
([ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md)); it happens only when you confirm it, every time.

1. Store the API key in the GNOME Keyring, never in a file (BR-005):
   [`store-cloud-api-key.md`](store-cloud-api-key.md). The review app also has a **Set API key** action once the
   cloud is enabled; it writes to the same keyring entry.
2. Choose the cloud as the provider: set `SUMMARIES_PROVIDER=cloud` (the Preferences window does the same).
   That is the whole opt-in; the button then says "(cloud)" and asks for confirmation before it sends anything.
   `SUMMARIES_CLOUD_ENABLED` no longer exists. Upgrading never switches the cloud on: a `SUMMARIES_PROVIDER=cloud` line in
   the environment or `.env` without `SUMMARIES_CLOUD_ENABLED=true` next to it (or with it `false`) keeps the provider
   local, logs one warning and locks the field in Preferences until you delete those lines; then choose Cloud in the
   window. `SUMMARIES_CLOUD_ENABLED=true` with the provider `local` no longer allows a per-request cloud summary (one
   button, one provider); it is ignored with a warning.
   `SUMMARIES_CLOUD_MODEL` accepts any model the gateway serves on `/chat/completions` (default `deepseek-v4-pro`).
3. Restart the service and press **Generate summary**; read the confirmation dialog and confirm only if you accept
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
| Install says `ollama: NOT found` | Ollama is not installed or not on `PATH`. Install it, then `ollama pull qwen3.5:9b`. |
| Install says `found but not reachable` | The server is stopped. `sudo systemctl start ollama` or `ollama serve`, then re-run `make install`. |
| Install says `model ... NOT found` | `ollama pull <model>` (the command is in the install output). The install never pulls. |
| `SUMMARY_PROVIDER_UNAVAILABLE` | Ollama is unreachable from the service. Start it; `SUMMARIES_LOCAL_BASE_URL` must point at it. |
| `SUMMARY_MODEL_NOT_FOUND` | The model is not in `ollama list`: pull it or fix `SUMMARIES_LOCAL_MODEL`. |
| `SUMMARY_CONTEXT_TOO_LARGE` | Raise the Ollama context and `SUMMARIES_LOCAL_CONTEXT_TOKENS` together (above). |
| `SUMMARY_OUTPUT_LIMIT_REACHED` | The model used its whole output limit, often on hidden reasoning. Raise `SUMMARIES_MAX_OUTPUT_TOKENS` or choose another model. |
| `SUMMARY_TIMEOUT` | The provider sent no data in time; retry, or raise `SUMMARIES_TIMEOUT_SECONDS`. A model that spills to the CPU is slow. |
| `SUMMARY_GPU_BUSY`, `SUMMARY_BUSY` | A recording, processing or another summary holds the GPU. Wait; processing resumes by itself. |
| `SUMMARY_CLOUD_DISABLED` | The provider is not `cloud`: set `SUMMARIES_PROVIDER=cloud` (or choose Cloud in Preferences). |
| `SUMMARY_CLOUD_KEY_MISSING`, `..._LOCKED`, `..._UNAVAILABLE` | Store, unlock or reach the keyring: see [`store-cloud-api-key.md`](store-cloud-api-key.md). |
| `SUMMARY_AUTH_FAILED`, `SUMMARY_RATE_LIMITED` | The gateway refused the key, or the plan limit is reached. Check the subscription and store the key again. |
| "Open last summary" does nothing | No app is set for Markdown files. `xdg-mime default <app>.desktop text/markdown`. |

Codes are listed in [`../architecture/error-codes-catalog.md`](../architecture/error-codes-catalog.md). To go back to
no summaries at all, leave the cloud off and stop Ollama; recording and processing never need either. `make uninstall`
leaves meetings, summaries and the settings file untouched.
