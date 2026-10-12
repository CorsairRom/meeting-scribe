---
type: spec
feature: summaries
status: approved  # draft | approved | in-progress | done | blocked
mvp: true
prd_ref: FR-6, NFR-1, NFR-2, NFR-3, NFR-6, safety requirements 1-3
depends_on:
  - decisions/006-summary-provider-openai-compatible.md (SummaryProvider port, one OpenAI-compatible adapter, privacy rules, reference structure)
  - decisions/009-cloud-summaries-through-opencode-go.md (the cloud gateway is OpenCode Go; replaces ADR-006's DeepSeek preset)
  - decisions/008-summary-sections.md (the summary has a Decisions section)
  - decisions/001-thin-gnome-extension-dbus-service.md (thin clients over the D-Bus session bus, no HTTP)
  - decisions/003-process-after-stop-single-gpu-worker.md (no GPU work during recording, 8 GB budget)
  - decisions/004-folder-per-meeting-json-storage.md (meeting.json, atomic writes, null object repository)
  - decisions/005-review-ui-native-gtk-app.md (the app shows the "cloud" label)
  - specs/review-app.md (the effective transcript and speaker names F5 summarizes; the app that hosts the button)
  - specs/preferences.md (the Preferences window and Settings1; product decision 9 removes SUMMARIES_CLOUD_ENABLED)
  - specs/processing.md (processing states, GPU policy, settings conventions)
  - architecture/api-contracts.md (Meetings1 today; Summaries1 is added to it)
verifiable_by:
  - tests/unit/summaries/
  - tests/unit/meetings/test_summary_controller.py
  - tests/e2e/meetings/test_summaries_over_dbus.py
  - tests/unit/review_app/test_summary_presenter.py
  - tests/unit/test_install_scripts.py
  - gnome-extension/tests/
last_updated: 2026-10-12
ai_instructions:
  - "This spec is a contract. Do NOT implement anything that is not in it."
  - "If you need to change the spec, update this doc FIRST and ask for confirmation."
  - "Every acceptance criterion must have a test that verifies it."
  - "The owner answered every product question on 2026-10-11 (see 'Product decisions'); they are decided, do not reopen them without asking."
  - "No test, and no agent, ever sends a request to Ollama or to a cloud provider. Tests use a fake SummaryProvider or an in-process fake transport (BR-001, BR-003, BR-004). A real run happens only on the user's explicit go, with a synthetic transcript."
  - "Transcript text, speaker names, summary text and the API key are private: never log them, never put them in an error message, a notification, a fixture that looks real or a commit message (BR-002)."
  - "Never read the .env file, the Hugging Face token or ~/Documents/meetings/."
---

# Spec: Summaries (generate, store and open a meeting summary)

## Goal (one sentence)

After a meeting is processed and reviewed, the user presses one button in the review app and gets a `summary.md`
(topics, decisions, action items, open questions) made by a local model by default or, only on an explicit and
confirmed request, by a cloud model clearly labelled "cloud", and can open it from the panel menu.

---

## Users / Personas

| Persona | How they use the feature |
|---|---|
| The developer (single user) | Names the speakers in the review app, presses "Generate summary", watches the progress, reads the result, optionally regenerates it with the cloud model after a visible confirmation, and later opens the last summary from the panel menu |

---

## User stories

- **US-1**: As the user, I want a "Generate summary" button for a reviewed meeting, so that I do not write minutes.
- **US-2**: As the user, I want the summary to use the speaker names I set, so that action items have owners.
- **US-3**: As the user, I want the local model by default and the cloud model only when I ask, so that I control what leaves my machine.
- **US-4**: As the user, I want the cloud option to be marked "cloud" everywhere it can be triggered and to ask me before every request, so that I never send a transcript by accident.
- **US-5**: As the user, I want to see progress and cancel a long summary, so that a slow model never traps me.
- **US-6**: As the user, I want the summary saved as a Markdown file next to the transcript, so that I can read, edit and share it without the app (PRD: export `.md`).
- **US-7**: As the user, I want to know when a summary is out of date because the meeting was renamed or reprocessed afterwards, so that I do not trust stale minutes.
- **US-8**: As the user, I want one menu click to open the last meeting's summary, labelled with a readable date, so that reading it costs no navigation.
- **US-9**: As the user, I want clear messages when Ollama is not running, a model is missing, the cloud key is absent or the GPU is busy, so that I know the next step.

---

## Acceptance criteria (Gherkin to tests)

Test paths are the planned locations; the task that builds each one is in [Task breakdown](#12-task-breakdown).
Every criterion runs against a fake provider or an in-process fake transport and a private bus. No test sends a
request to Ollama or to a cloud provider, opens an audio device or reads a real meeting.

### AC-1: A summary is generated for a finished meeting

```gherkin
GIVEN a DONE meeting with utterances and the local provider
WHEN a client calls Summarize(meeting, "local", false)
THEN the call returns "RUNNING" at once, without waiting for the model
AND SummaryProgress(meeting, step, total) is emitted as steps complete
AND on success summary.md exists in the meeting folder, meeting.json holds the summary section and
    SummaryFinished(meeting, "DONE", "") is emitted after both are saved
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_summarize_saves_the_file_then_the_section`, `tests/e2e/meetings/test_summaries_over_dbus.py::test_summarize_signals`

### AC-2: Only a DONE meeting can be summarized

```gherkin
GIVEN a meeting that is QUEUED, TRANSCRIBING, DIARIZING, EXTRACTING_SAMPLES, FAILED or UNSUPPORTED
WHEN a client calls Summarize
THEN the call fails with MEETING_NOT_PROCESSED and nothing is written
GIVEN a DONE meeting with zero utterances
THEN the call fails with SUMMARY_NO_SPEECH and no provider is called
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_refused_unless_done`, `::test_no_speech_is_not_summarized`

### AC-3: The summary uses the effective transcript

```gherkin
GIVEN speaker S1 named "Alex" and S2 merged into S1
WHEN a summary is generated with the local provider
THEN the transcript given to the provider shows "Alex" for every line of S1 and S2, with timestamps, in time order
AND the Participants line of summary.md lists each active speaker once, using the display name
AND the date, time and duration in the header come from meeting.json, not from the model
```

**Test**: `tests/unit/summaries/test_prompt_input.py::test_effective_names_and_merges`, `tests/unit/summaries/test_render_summary.py::test_header_is_computed_locally`

### AC-4: Long transcripts are summarized in steps

```gherkin
GIVEN a synthetic transcript whose estimated size exceeds the provider's usable context
WHEN a summary is generated
THEN the transcript is split on utterance boundaries into chunks that each fit the budget
AND each chunk is summarized into partial notes, and the notes are merged into the final summary
AND when the notes still exceed the budget they are merged in batches until they fit
AND no request exceeds the budget and no utterance is dropped or duplicated
GIVEN a transcript that fits
THEN exactly one request is made
```

**Test**: `tests/unit/summaries/test_chunker.py`, `tests/unit/summaries/test_generate_summary.py::test_map_reduce_steps`, `::test_single_pass_when_it_fits`

### AC-5: Cloud is opt-in (the provider choice), keyed and confirmed

```gherkin
GIVEN the configured provider is local (the cloud is opted into only by choosing it as the provider)
WHEN a client calls Summarize(meeting, "cloud", true)
THEN it fails with SUMMARY_CLOUD_DISABLED and no request is made
GIVEN the configured provider is cloud but the keyring holds no API key
THEN it fails with SUMMARY_CLOUD_KEY_MISSING
GIVEN the keyring is locked
THEN it fails with SUMMARY_CLOUD_KEY_LOCKED and the service never prompts
GIVEN the configured provider is cloud and keyed
WHEN cloud_confirmed is false
THEN it fails with SUMMARY_CLOUD_NOT_CONFIRMED and no request is made
GIVEN an unknown provider id
THEN it fails with INVALID_VALUE
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_cloud_gates`

### AC-6: Providers are described by the service, including the "cloud" label

```gherkin
WHEN a client calls Providers
THEN it gets one row per configured provider with id, label, cloud, model, available and reason
AND the cloud row's label contains the word "cloud" and its available is false with a reason code when the
    the configured provider is local (SUMMARY_CLOUD_DISABLED)
AND no row contains the API key or the base URL of the cloud provider
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_providers_rows`, `tests/e2e/meetings/test_summaries_over_dbus.py::test_providers`

### AC-7: What is sent to the cloud

```gherkin
GIVEN a request to the cloud provider (confirmed)
THEN the messages contain the instructions, the date and duration, and the transcript lines
AND speaker names are sent as written (product decision 3)
AND the messages contain no meeting id, folder name, file path, user name, host name or API key
AND the request is logged once at INFO with the meeting id, provider id and model, never any content
GIVEN a request to the local provider
THEN the same content is sent (the request never leaves loopback)
```

**Test**: `tests/unit/summaries/test_generate_summary.py::test_cloud_request_payload_has_no_identifiers` (fake provider records its messages)

### AC-8: The local provider never leaves loopback

```gherkin
GIVEN SUMMARIES_LOCAL_BASE_URL has a host that is not 127.0.0.1, ::1 or localhost
WHEN the service loads its settings
THEN it fails at load with an error naming the variable
GIVEN SUMMARIES_CLOUD_BASE_URL does not use https
THEN it fails at load
```

**Test**: `tests/unit/summaries/test_summaries_settings.py`

### AC-9: The API key never leaks

```gherkin
GIVEN an API key stored in the GNOME Keyring (ADR-009)
THEN no settings class holds it, and the store hands it out as a SecretStr whose repr is masked
AND no log record, D-Bus reply, error message, meeting.json or summary.md produced in any test contains it
AND the key is sent only in the Authorization header of the cloud request
```

**Test**: `tests/unit/summaries/test_summaries_settings.py::test_key_is_masked`, `tests/unit/summaries/test_openai_adapter.py::test_key_only_in_the_header` (in-process fake transport), `tests/unit/meetings/test_summary_controller.py::test_no_secret_in_logs` (caplog)

### AC-10: Progress, cancel and timeouts

```gherkin
GIVEN a running summary
WHEN a client calls CancelSummary(meeting)
THEN the in-flight request is aborted, no file is written, the previous summary (if any) is untouched
AND SummaryFinished(meeting, "CANCELLED", "SUMMARY_CANCELLED") is emitted and the call returns true
GIVEN the provider sends nothing for SUMMARIES_TIMEOUT_SECONDS
THEN the step fails with SUMMARY_TIMEOUT
GIVEN the whole summary exceeds SUMMARIES_TOTAL_TIMEOUT_SECONDS
THEN it fails with SUMMARY_TIMEOUT
GIVEN a user cancel and an expired total deadline hold at the same time
THEN the user's cancel wins (SUMMARY_CANCELLED)
GIVEN no time is left of the total bound
THEN the run fails with SUMMARY_TIMEOUT without calling the provider
AND every provider call receives a positive time left, never below a small minimum
GIVEN the service shuts down during a summary
THEN the request is aborted and nothing is persisted (a summary is never resumed)
```

**Time sources**: the injected clock (monotonic by default) decides the deadline and the time left per call; a
real-time timer only wakes a stalled provider call, since only real time can interrupt a blocked call.

**Test**: `tests/unit/summaries/test_generate_summary.py::test_cancel_between_and_during_requests`, `test_a_user_cancel_wins_over_an_expired_deadline`, `test_an_exhausted_budget_times_out_before_calling_the_provider`, `test_the_per_call_time_is_never_below_the_minimum`, `tests/unit/meetings/test_summary_controller.py::test_shutdown_aborts`

### AC-11: One summary at a time

```gherkin
GIVEN a summary is running for meeting A
WHEN Summarize is called for A
THEN it fails with SUMMARY_ALREADY_RUNNING
WHEN Summarize is called for meeting B
THEN it fails with SUMMARY_BUSY
AND ActiveSummaryMeetingId is A until SummaryFinished
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_single_slot`

### AC-12: GPU contention with recording and processing

```gherkin
GIVEN a recording is active or a processing job is running
WHEN Summarize is called with the local provider
THEN it fails with SUMMARY_GPU_BUSY and the message says to try again when processing ends
WHEN it is called with the cloud provider (confirmed)
THEN the GPU rule does not apply
GIVEN a local summary is running
WHEN a processing job becomes ready or a recording starts
THEN no worker starts until the summary ends, and a recording start cancels the summary
     (SummaryFinished CANCELLED) so that recording is never delayed (ADR-003)
GIVEN a local summary ends (done, failed or cancelled) and SUMMARIES_UNLOAD_LOCAL_MODEL is true
THEN the model is released from the GPU before the next worker may start
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_local_summary_respects_the_gpu`, `tests/unit/meetings/test_processing_controller.py::test_no_worker_while_a_local_summary_runs`, `tests/unit/summaries/test_openai_adapter.py::test_release_after_local_use`

### AC-13: Provider failures have stable codes and next steps

```gherkin
GIVEN the provider is unreachable, the model is unknown, the key is rejected, the rate limit persists,
      the output is empty or malformed, or the output was cut at the token limit
THEN the summary fails with, respectively, SUMMARY_PROVIDER_UNAVAILABLE, SUMMARY_MODEL_NOT_FOUND,
     SUMMARY_AUTH_FAILED, SUMMARY_RATE_LIMITED, SUMMARY_OUTPUT_INVALID, SUMMARY_OUTPUT_LIMIT_REACHED
AND transient failures (connection, 408, 409, 429, 5xx) are retried up to SUMMARIES_MAX_RETRIES with backoff first
AND the failure is saved in meeting.json (code and time, never content) and any previous summary is kept
AND the error message names the next step (start Ollama, pull the model, check the key) and never quotes content
```

**Test**: `tests/unit/summaries/test_openai_adapter.py::test_error_mapping`, `tests/unit/meetings/test_summary_controller.py::test_failure_is_recorded_and_previous_kept`

### AC-14: Silent context truncation is detected

```gherkin
GIVEN a local response whose usage.prompt_tokens is within 16 tokens of the configured context window
THEN the step fails with SUMMARY_CONTEXT_TOO_LARGE and the message names the two settings to raise together
     (the Ollama context and SUMMARIES_LOCAL_CONTEXT_TOKENS)
```

**Test**: `tests/unit/summaries/test_openai_adapter.py::test_truncation_guard`

### AC-15: Regeneration keeps one backup; staleness is computed

```gherkin
GIVEN a meeting with a summary
WHEN a new summary succeeds
THEN the old summary.md becomes summary.previous.md (one backup, replaced each time) and the new one is written atomically
GIVEN the speakers were renamed or merged, or the meeting was reprocessed, after the summary was made
WHEN a client calls GetSummary or GetSummaryStatus
THEN stale is true (the stored input fingerprint differs from the current effective transcript)
AND summary.md is not modified or deleted
GIVEN a failed regeneration
THEN summary.md is untouched
```

**Test**: `tests/unit/meetings/test_summary_controller.py::test_backup_rotation`, `::test_stale_is_computed_from_the_fingerprint`, `tests/unit/meetings/test_meeting.py::test_reprocess_keeps_the_summary_section`

### AC-16: The summary is served as data and the menu gets a path only through the service

```gherkin
GIVEN a DONE meeting with a summary
WHEN a client calls GetSummary(meeting)
THEN it gets the markdown text, title, generated_at, provider, provider_label, cloud, model, language and stale
AND it never needs a path
WHEN a client calls LastSummary
THEN it gets meeting_id, started_at, generated_at and the absolute path of the newest summary
AND no client hard-codes a file name or a folder layout
GIVEN no meeting has a summary
THEN GetSummary and LastSummary fail with ENTITY_NOT_FOUND_SUMMARY
```

**Test**: `tests/e2e/meetings/test_summaries_over_dbus.py::test_get_summary`, `::test_last_summary`

### AC-17: The review app generates and shows the summary

```gherkin
GIVEN a DONE meeting selected in the review app
THEN the app shows a Summary view with the state: none, running (progress and Cancel), done, failed or out of date
AND there is exactly one "Generate summary" button, which uses the provider chosen in the settings
    (SUMMARIES_PROVIDER, reported by Providers as the default row)
GIVEN the configured provider is the cloud
THEN the caption under the button names the cloud and the model, for example "Cloud · deepseek-v4.1-flash"
WHEN the user presses it
THEN a dialog states that the transcript will leave this computer and goes to <provider>, with Cancel as the
     default and "Send to cloud" as the explicit action; only that action calls Summarize(..., true)
AND a summary made by the cloud shows a "cloud" badge with the provider name for as long as it is displayed
GIVEN the user closes the dialog, presses Escape or Cancel
THEN no request is made
GIVEN the configured provider is the local one
THEN the button label names the local model and no dialog is shown
```

**Test**: `tests/unit/review_app/test_summary_presenter.py`, `tests/unit/review_app/test_cloud_confirmation.py`

### AC-18: The review app errors have messages with a next step

```gherkin
GIVEN any summary error code a call or SummaryFinished can carry
THEN the app shows the message mapped to that code and, for an unmapped code, a generic message with the code
AND a drift test fails when a code of the catalog has no message
AND no transcript text, name or summary text is written to the log
```

**Test**: `tests/unit/review_app/test_error_messages.py` (extended drift test)

### AC-19: The panel menu opens the last summary

```gherkin
GIVEN the service is available and LastSummary succeeds
THEN the menu shows "Open last summary (<readable date and time>)" built from started_at in the user's locale
AND the label contains no title and no content
WHEN the user activates it
THEN the extension opens the file returned by the service with the default handler for Markdown
GIVEN LastSummary fails with ENTITY_NOT_FOUND_SUMMARY or UnknownMethod (older service)
THEN the item is hidden
GIVEN the file cannot be opened
THEN the extension shows one notification with a short message
AND the extension never reads meeting content and never builds a path
```

**Test**: `gnome-extension/tests/indicatorModel.test.js`, `gnome-extension/tests/indicatorController.test.js`, `gnome-extension/tests/meetingsClient.test.js`

### AC-20: Install reports Ollama without failing

```gherkin
GIVEN make install runs
THEN the summary says whether Ollama is found and reachable and whether the configured local model is present
AND a missing or stopped Ollama, or a missing model, only warns with the command to fix it
AND the install never starts Ollama, never pulls a model and never sends a prompt
GIVEN make uninstall runs
THEN meetings, summaries and .env are untouched
```

**Test**: `tests/unit/test_install_scripts.py` (fake `ollama` on PATH)

### AC-21: Import contracts and isolation

```gherkin
GIVEN the summaries package
WHEN make imports runs
THEN summaries imports no other domain and no meetings code
AND summaries.domain imports no openai, pydantic_settings or dbus_fast
AND openai is imported only in summaries.infrastructure
AND meetings reaches summaries only through summaries.application
```

**Test**: `make imports` (contracts in section 6)

### AC-22: Summary language and technical terms

```gherkin
GIVEN SUMMARIES_LANGUAGE is auto and the meeting language is detected as X
THEN the prompt asks for the body in X and carries the headings of X (or the English ones, AC-23)
GIVEN SUMMARIES_LANGUAGE is an ISO code
THEN that code wins over the detected language
AND in every case the prompt contains the rule that technical terms are kept as spoken and never translated
AND the map and reduce prompts carry the same rule
```

**Test**: `tests/unit/summaries/test_prompt_builder.py::test_language_and_technical_terms_rule`

### AC-23: Localized headings through a catalog

```gherkin
GIVEN the meeting language is Spanish
THEN the prompt states the Spanish headings, the model output is validated against them
AND summary.md is rendered with them (Temas, Decisiones, Tareas, Preguntas abiertas and the fixed labels)
GIVEN the meeting language has no catalog (or the code is not a plain lowercase code)
THEN the headings and labels are English and the body is still written in the meeting language
AND the model is told to copy the headings as shown and not to translate them
GIVEN a catalog is missing, unreadable or broken
THEN the headings are English and nothing fails
AND no Spanish text exists in code or tests: only in the Spanish catalog file
```

**Test**: `tests/unit/summaries/test_gettext_headings.py`, `tests/unit/summaries/test_generate_summary.py::test_localized_headings_are_asked_for_validated_and_rendered`, `::test_an_unlisted_language_gets_english_headings_and_a_body_language_request`

---

## What this is NOT (out of scope for this spec)

- Not chat with the meeting, search across meetings or Q and A (PRD out of scope; IDEA-002).
- Not automatic summaries (product decision 2: manual only) and not a notification when a summary ends.
- Not editing the summary in the app: the file is plain Markdown and the user edits it in any editor.
- Not rich Markdown rendering in the app: the Summary view is a read-only text view with light heading styling.
- Not more than one cloud provider in this phase: the cloud is an OpenAI-compatible gateway, default OpenCode Go (ADR-009); another gateway is only a settings change. Models served only on `/responses` (Grok, GPT) or `/messages` (MiniMax) are not supported.
- Not vendor-specific features (structured output, tools, thinking modes): ADR-006 trigger to revisit.
- Not a summary history: one current file and one backup (product decision 5).
- Not installing, starting, updating or pulling models for Ollama: that stays with the user.
- Not translating an existing summary, and not summarizing audio directly.
- Not readable folder names from the summary title (backlog "Later improvements"); the title is stored in `meeting.json` so that item can use it later.
- Not an HTTP surface and not direct reads of meeting files by the app (ADR-001, ADR-005).

---

## Edge cases

| Case | Expected behavior |
|---|---|
| Meeting with no speech | `SUMMARY_NO_SPEECH`; the app shows "Nothing to summarize" and no button |
| Meeting with only "Me" | Summarized normally; Participants lists "Me" |
| Speaker never named | Shown with the default label ("Speaker 2"); the app suggests naming speakers first but never blocks |
| Very long meeting (4 h) | Chunked map-reduce (AC-4); progress shows "step n of m"; cancel works between and during requests |
| One utterance longer than the chunk budget | Split on sentence boundaries, then on characters; never dropped |
| Model answers with its reasoning (a "thinking" model) | Only the message content is used; a leading `<think>...</think>` block is stripped; the reasoning is never stored |
| Model returns text without the expected sections | One repair request, then `SUMMARY_OUTPUT_INVALID`; nothing is written |
| Model returns an empty or whitespace string | `SUMMARY_OUTPUT_INVALID` |
| Output reaches `SUMMARIES_MAX_OUTPUT_TOKENS` (`finish_reason` length), with empty or partial content | `SUMMARY_OUTPUT_LIMIT_REACHED` (not a context problem; cut output is never saved as complete) |
| The model translates a technical term (for example it renders "deploy" in the summary language) | Not detected by the service; the prompt contract forbids it (AC-22) and the validation in F5.9 checks real output |
| Ollama stopped (the system service is inactive) | `SUMMARY_PROVIDER_UNAVAILABLE` with "Start Ollama (systemctl status ollama)" |
| Ollama model not pulled | `SUMMARY_MODEL_NOT_FOUND` with `ollama pull <model>` (the model name is configuration, not content) |
| Two clients press Generate together | One wins; the other gets `SUMMARY_ALREADY_RUNNING` or `SUMMARY_BUSY` |
| Meeting is renamed or merged while a summary runs | The summary completes with the fingerprint of the input it used, so it shows as out of date at once |
| Meeting is reprocessed while a summary runs | Reprocess queues the meeting, so it is no longer DONE when the summary tries to save: the result is discarded and the summary fails with `MEETING_NOT_PROCESSED`; the old summary stays |
| Local summary running, recording starts | The summary is cancelled (AC-12); the user presses Generate again later |
| Service restarted by `make install` during a summary | The request is aborted, nothing persisted; the app shows the not-running state and then "No summary yet" or the old one |
| `summary.md` deleted by the user | `GetSummary` and `LastSummary` skip it (state NONE) and the stale `summary` section is ignored; Generate works again |
| `summary.md` edited by the user, then Generate again | The edited file becomes `summary.previous.md`; nothing is lost (product decision 5) |
| `meeting.json` from a newer schema | Listed `UNSUPPORTED`; no summary call works; the file is never rewritten |
| Cloud selected, key present, no network | `SUMMARY_PROVIDER_UNAVAILABLE`; nothing is queued for later |
| Cloud returns an auth or balance error | `SUMMARY_AUTH_FAILED` for 401 and 403; any other 4xx is `SUMMARY_PROVIDER_REJECTED` with the HTTP status only |
| Storage moves to the XDG layout (backlog) | No client change: clients get data or a path from the service, never build one |
| Language setting is not an ISO code | Rejected at settings load |

---

## Technical design

### 1. Architecture

```mermaid
flowchart LR
  EXT["GNOME extension<br/>menu: Open last summary"] -- "LastSummary, then open the returned file" --> SVC
  subgraph APP["Review app (system Python)"]
    VIEW["Summary view<br/>Generate / cloud confirm / progress"] --> PRES["core: summary presenter,<br/>cloud confirmation, error messages"]
  end
  APP -- "Summaries1: Providers, Summarize,<br/>CancelSummary, GetSummary(Status)" --> SVC
  subgraph SVC["Service (venv, Python 3.12)"]
    IFACE["Summaries1 adapter<br/>(meetings/infrastructure/dbus)"] --> SC["meetings: SummaryController<br/>(state check, lock, GPU gate, one job thread, files)"]
    SC --> GS["summaries: GenerateSummary<br/>(chunk, map, reduce, render)"]
    GS --> PORT["SummaryProvider port"]
    PORT --> AD["OpenAI-compatible adapter<br/>(openai client, streaming)"]
  end
  AD -- "loopback" --> OLL["Ollama (local, default)"]
  AD -- "https, explicit and confirmed" --> DS["OpenAI-compatible cloud gateway<br/>(default OpenCode Go, opt-in)"]
  SC --> FS[("meeting folder<br/>summary.md, summary.previous.md,<br/>meeting.json summary section")]
```

Decisions in one line each (details in the sections that follow):

1. **The service calls the provider, never a client** (ADR-001, thin clients). The API key lives only in the GNOME Keyring and is read by the service on demand (ADR-009).
2. **One manual action per summary**: a button in the review app; nothing runs automatically (product decision 2). The menu only opens the result.
3. **`summaries` is a pure domain plus one adapter**: the port, chunking, prompts, rendering and fingerprint are pure; `meetings` owns meeting state, the lock and the files, and hands `summaries` plain data (CR-001, CR-003).
4. **One button, one configured provider, the cloud gated four times**: the button uses `SUMMARIES_PROVIDER` (default local); the cloud needs the provider choice (the opt-in, decision 9), a key, the `cloud_confirmed` argument and a confirmation dialog on every request (ADR-006, BR-003, BR-004). Names go to the cloud as written. The "cloud" label comes from the service (`Providers`) and is on the button, so no client can forget it.
5. **Local and GPU**: a local summary never runs while recording or processing, blocks the next worker while it runs, and releases the model afterwards (ADR-003, NFR-3).
6. **Staleness is computed, not stored**: the summary stores a fingerprint of the transcript it used; Reprocess and renames never touch `summary.md`.

### 2. Where "Generate summary" lives and when it may run

**Decision: one button in the review app's Summary view, manual only, on `DONE` meetings.**

| Option | Verdict |
|---|---|
| Review app button | Chosen: the user has just named the speakers; the cloud confirmation dialog and the "cloud" label belong in a window, not in a panel menu; the extension stays thin (no content, no dialogs) |
| Panel menu action "Generate summary" | Rejected: no room for the confirmation and label; the menu would need to pick a meeting |
| Automatic after processing | Rejected (product decision 2): it would start before the user names speakers (names are in the summary), take the GPU for minutes while the next meeting may be recording, and the cloud must never start by itself |

It may run only on a `DONE` meeting (`MEETING_NOT_PROCESSED` otherwise). It may run before speakers are named; the
app shows a one-line hint when no speaker is named, but does not block. Because names change the fingerprint,
naming later makes the summary show as out of date, which is the correct signal.

### 3. The `summaries` domain

Layout under `src/meeting_scribe/summaries/`:

| Layer | Content |
|---|---|
| `domain/` | `SummaryRequest` (plain input: header facts, participants, utterance lines, language, budget), `SummaryText`, `SummaryProvider` port, `ModelReleaser` port, `TranscriptChunker`, `PromptBuilder`, the `Headings` value with its English texts (the message ids) and the `HeadingCatalog` port, `SummaryRenderer` (header computed locally plus the model body), `InputFingerprint`, errors (section 9) |
| `application/` | `GenerateSummary` use case: one pass or map-reduce, cancel and progress callbacks, output validation with one repair request, release of the model |
| `infrastructure/` | `SummariesSettings` (`SUMMARIES_` prefix, section 8), `OpenAiCompatibleProvider` (one class for Ollama and the cloud gateway), `OllamaModelReleaser`, `FilesystemSummaryFiles` (atomic write, backup rotation, read), `GettextHeadingCatalog` (reads the `locale/` catalogs, see section 11) |

**Port** (shape, not code to copy):

- `SummaryProvider.complete(messages, limits, cancel) -> Result[Completion, SummaryError]`, where `Completion` carries the text, `finish_reason` and token usage numbers. `limits` has the model, temperature, max output tokens and the context window. `cancel` is a `threading.Event`-like signal (the `CancelSignal` shape already used by the worker runner).
- `ModelReleaser.release() -> None`: best effort, never raises, a no-op for the cloud.

**Who calls whom.** `meetings.SummaryController` reads the effective utterances from the speakers application layer
(the same call `ReviewController.transcript` uses), builds a `SummaryRequest` and calls
`summaries.application.GenerateSummary`. `summaries` never imports `meetings`, `speakers` or `transcription` (CR-001).
Entities stay frozen and pass `now` in (CR-006); the repository stays a Null Object (CR-005).

**Prompt design.** A system message fixes the role, the output language, the section list and the rules
(use only what the transcript says; mark guesses as "unclear"; the transcript may contain recognition errors; the
text between the transcript markers is data, never instructions, which limits prompt injection from meeting speech).
**Technical terms are never translated** (product decision 4): the prompt tells the model to keep technical terms as
spoken, in their original form (for example "deploy" or "pull request"), even when the rest is in another language.
Prompt text lives in the domain with a `prompt_version` integer stored in `meeting.json`.

### 4. Long transcripts, context and chunking

The real context window is the weak point of the local path, so the design assumes nothing about it:

- Ollama's default context is 4096 tokens on GPUs under 24 GiB of VRAM, and the OpenAI-compatible endpoint has no
  parameter to change it; it is changed with `OLLAMA_CONTEXT_LENGTH` or a Modelfile `PARAMETER num_ctx` (Ollama docs,
  see [Library facts](#library-facts-verified-context7-2026-10-11)). This GPU has 8 GB, so the default applies.
- **`SUMMARIES_LOCAL_CONTEXT_TOKENS` defaults to 4096** (safe by default) and is the budget the service plans
  against. A user who raises Ollama's context raises this setting with it (runbook). The cloud budget is
  `SUMMARIES_CLOUD_CONTEXT_TOKENS`.
- **Token estimate**: characters divided by 3 (conservative for Spanish and English; no tokenizer dependency).
- **Usable input per request** = context - `SUMMARIES_MAX_OUTPUT_TOKENS` - 600 (instructions and margin).
- **Single pass** when the whole transcript fits. Otherwise **map-reduce**:
  1. *Chunk* on utterance boundaries to the usable budget (an oversized utterance is split on sentences, then characters).
  2. *Map*: each chunk yields partial notes (topics, decisions, action items with owner, open questions) in compact Markdown.
  3. *Reduce*: the notes are merged into the final sections; if the notes exceed the budget they are merged in
     batches, level by level, until one request remains.
- **Progress**: `total` is known after chunking (chunks plus the reduce steps planned so far) and may grow by the
  extra batch levels; the app shows "step n of m".
- **Truncation guard** (AC-14): Ollama can silently drop the start of a prompt that exceeds its real window. If
  `usage.prompt_tokens` is within 16 of the configured window, the step fails instead of producing a summary of half
  a meeting. This catches a user who set the Ollama context equal to the setting; the setting being higher than the
  real window is caught by the same guard only when the prompt actually reaches the real window (documented in the
  runbook: check `ollama ps`, the CONTEXT column).
- Quality note: a 1-hour meeting at 4096 tokens is roughly 9 chunk requests plus the reduce. It is slow but correct;
  raising the context (8192 fits an 8 GB card, assumption A-3) cuts the steps.

### 5. The provider adapter (Ollama default, cloud gateway opt-in)

One class, `OpenAiCompatibleProvider`, configured by `base_url`, `model`, a `cloud` flag and, for the cloud, a `CloudKeyStore` (ADR-006, ADR-009).
It uses the official `openai` Python client (new dependency, assumption A-5):

- **Endpoint**: `chat.completions.create(model=..., messages=..., temperature=..., max_tokens=..., stream=True)`.
  Ollama's `/v1/chat/completions` supports `messages`, `temperature`, `max_tokens`, `stream`, `seed` and
  `response_format`; the Ollama API key is required but ignored, so the local provider sends a fixed placeholder
  (verified in the Ollama docs). The cloud gateway (default OpenCode Go, ADR-009) uses `base_url="https://opencode.ai/zen/go/v1"`, `POST /chat/completions` and a Bearer key; only models served on `/chat/completions` work (DeepSeek, GLM, Kimi, MiMo, LongCat), not those on `/responses` or `/messages`.
- **Streaming** is used for both: it lets cancellation abort the HTTP request between deltas (closing the stream),
  and makes the read timeout mean "no data for N seconds" instead of "the whole generation took N seconds", which
  matters for a slow local model.
- **Timeouts**: connect 5 s fixed; read `SUMMARIES_TIMEOUT_SECONDS` (300) between data; a whole summary is bounded by
  `SUMMARIES_TOTAL_TIMEOUT_SECONDS` (3600). The client supports a per-client float or an `httpx` timeout object and
  raises `APITimeoutError` (verified).
- **Retries**: the client retries connection errors, 408, 409, 429 and 5xx with backoff; `max_retries` is set from
  `SUMMARIES_MAX_RETRIES` (default 2; the client's own default is also 2, verified). A failure in the middle of a
  stream is not retried by the client, so the adapter retries that step once.
- **Error mapping** (section 9) uses the client's exception classes: connection error, timeout, rate limit, status
  error with `status_code`. Messages carry the HTTP status or the exception class, never request or response bodies.
- **Reasoning output**: only `message.content` is used; a leading `<think>...</think>` block is stripped.
- **Client and session identification** (provider-level option, cloud only): OpenCode Go answers HTTP 400
  `MissingSessionID` without it (source: <https://opencode.ai/docs/go>, "Where can I use it"). Every cloud request,
  including the `/models` listing, carries `User-Agent: meeting-scribe/<package version>`; every chat request carries
  `x-opencode-session: <id>`, an opaque id (the first 32 hex characters of the SHA-256 of a random UUID) created once
  per summary job and shared by its steps. It is never derived from the meeting id, names or content and never
  logged. The header name is a field of the provider entry in `CLOUD_PROVIDERS` (`session_header`); the local
  provider sends neither header.
- **Reasoning effort** (provider-level option): the local provider sends `"reasoning_effort": "none"` (the `openai`
  client's `reasoning_effort` parameter) so a thinking model such as `qwen3.5:9b` does not spend the whole output
  budget on hidden reasoning and answer with nothing (verified on Ollama 0.18: 2048 reasoning tokens, empty content,
  `finish_reason: length` without it; 4 s and a correct summary with it). The cloud provider does not send it.
- **Releasing the GPU** (`OllamaModelReleaser`): after a local summary ends in any way, and when
  `SUMMARIES_UNLOAD_LOCAL_MODEL` is true, the service asks Ollama to unload the model with a native
  `POST <origin>/api/generate {"model": ..., "keep_alive": 0}` (documented by Ollama as the immediate unload; the
  OpenAI endpoint has no such parameter). It is a loopback call, best effort, logged on failure by code only. This is
  the one non-OpenAI call and it exists only to protect the 8 GB budget (Whisper must be able to load next).
  Without it Ollama keeps the model loaded for its idle period (`OLLAMA_KEEP_ALIVE`; its default is not verified
  here), which can be long enough to make the next transcription fail with `GPU_OUT_OF_MEMORY`.
- **Service process stays free of ML**: the adapter is an HTTP client; AC-20 of the processing spec still holds.

### 6. Runtime, import contracts and dependencies

- **New dependency**: `openai` in the service's base dependencies (not the `ml` extra). Alternative considered:
  call the endpoints with `httpx2` directly (already in the lock): fewer dependencies, but hand-written SSE
  streaming, retry and error mapping that the client already provides. Recommended: `openai` (assumption A-5).
- **New import-linter contracts** (listed in `ARCHITECTURE.md` section 3 in the same commit):
  - `summaries.domain` and `summaries.application` import neither `openai`, `httpx`, `pydantic_settings` nor `dbus_fast`.
  - `openai` is importable only from `summaries.infrastructure`.
  - The existing "domains are independent" and "meetings reaches domains only through application" contracts already list `summaries`.
- **Composition**: `main.py` builds `SummariesSettings`, the provider(s), the releaser and the `SummaryController`,
  and registers `Summaries1` beside `Recording1` and `Meetings1`. Domains never import settings (CR-007).

### 7. The `meetings` side: state, files and the controller

**`meeting.json` additions** (additive, `schema_version` stays 1; an older service round-trips them through
`extra="allow"`, GO-032):

```json
"summary": {
  "file": "summary.md",
  "generated_at": "2026-10-11T10:30:00+00:00",
  "provider": "local",
  "cloud": false,
  "model": "<model name>",
  "language": "es",
  "title": "<model-written short title>",
  "input_fingerprint": "sha256:<hex>",
  "chunks": 3,
  "prompt_version": 2,
  "last_failure": {"error_code": "SUMMARY_TIMEOUT", "at": "2026-10-11T10:40:00+00:00"}
}
```

`files["summary"]` is set to `summary.md`. The section is absent until the first success. A running summary is
**not persisted** (it is not resumable; a restart clears it). `Meeting` gets a frozen `SummaryInfo` section with
`with_summary(...)` and `with_summary_failure(...)` transitions that return `model_copy` copies (CR-006).
`Meeting.reprocess` and `complete` leave `summary` untouched (AC-15).

**`SummaryController`** (in `meetings/application/`, same style as `ReviewController`):

- Takes the shared `MeetingLock` **only to snapshot and to save**, never during the model call, so processing and
  review calls are not blocked for minutes.
- Snapshot: check `DONE`, read the effective utterances and names, compute the fingerprint, check the gates (cloud,
  GPU, single slot), start the job thread, return `RUNNING`.
- Save (after the model succeeds): take the lock, re-check the meeting is still `DONE`, rotate the backup, write
  `summary.md` atomically (`os.replace`), save `meeting.json` with the section, release the lock, emit
  `SummaryFinished`. Order: file first, then `meeting.json`; a crash between them leaves a file without a section,
  which `GetSummary` treats as NONE and a later Generate overwrites (no stale-flag dance needed).
- Staleness: `stale = section.input_fingerprint != fingerprint(current effective transcript)`.
- **Fingerprint**: SHA-256 over a canonical JSON of `(display name, start, end, text)` per effective utterance. It
  does not include provider, language or model.

**GPU gate with processing** (AC-12): the controller and `ProcessingController` share a small `GpuLease` port
in `meetings/domain/ports.py`: a recording or a worker holds it for the GPU stages; a local summary holds it for the
whole job. `ProcessingController._can_start` already returns false while a recording is active; it additionally
returns false while a local summary holds the lease, and `preempt()` (recording started) also cancels a local
summary. A local `Summarize` fails with `SUMMARY_GPU_BUSY` when the lease is taken. This reuses the existing
single-GPU-job rule (ADR-003, ADR-007) instead of adding a second scheduler. The cloud path takes no lease.

### 8. Settings

`SummariesSettings` in `summaries/infrastructure/summaries_settings.py`, prefix `SUMMARIES_`, composed into `Settings`.
New rows are added to `.env.example` and `ARCHITECTURE.md` section 7 in the same commit. The API key is **not a
setting**: it is read from the GNOME Keyring through the `CloudKeyStore` port (section 8a), never logged (AC-9).

| Variable | Default | Meaning |
|---|---|---|
| `SUMMARIES_PROVIDER` | `local` | The provider the "Generate summary" button uses, `local` or `cloud` (product decision 8). Choosing `cloud` is the cloud opt-in (product decision 9); it also needs a key |
| `SUMMARIES_LOCAL_BASE_URL` | `http://127.0.0.1:11434/v1` | Ollama's OpenAI-compatible endpoint; the host must be loopback (AC-8) |
| `SUMMARIES_LOCAL_MODEL` | `qwen3.5:9b` | Ollama model name (installed on the owner's machine; install only checks it exists) |
| `SUMMARIES_LOCAL_CONTEXT_TOKENS` | `4096` | The window the service plans against; raise it together with Ollama's context (section 4) |
| `SUMMARIES_UNLOAD_LOCAL_MODEL` | `true` | Ask Ollama to unload the model after a local summary (AC-12) |
| `SUMMARIES_CLOUD_ENABLED` | removed | Removed by product decision 9: the cloud is on only when `SUMMARIES_PROVIDER=cloud`. The migration rule is in [`preferences.md`](preferences.md) section 7 |
| `SUMMARIES_CLOUD_BASE_URL` | `https://opencode.ai/zen/go/v1` | OpenAI-compatible cloud gateway (default OpenCode Go, ADR-009); must be https |
| `SUMMARIES_CLOUD_MODEL` | `deepseek-v4-pro` | Any model the gateway serves on `/chat/completions`; not those on `/responses` or `/messages` (assumption A-1) |
| `SUMMARIES_CLOUD_CONTEXT_TOKENS` | `100000` | Conservative budget (the published figures disagree, A-2) |
| `SUMMARIES_CLOUD_PROVIDER` | `opencode-go` | The cloud provider choice shown in Preferences as "Provider" (one option, "OpenCode Go"); it decides the default of the two settings below |
| `SUMMARIES_CLOUD_PROVIDER_NAME` | `OpenCode Go` | Name shown in the "cloud" label (`cloud: <name>, <model>`) |
| `SUMMARIES_LANGUAGE` | `auto` | `auto` follows the meeting language (product decision 4), or an ISO code |
| `SUMMARIES_MAX_OUTPUT_TOKENS` | `2048` | Output limit per request (>= 256) |
| `SUMMARIES_TEMPERATURE` | `0.2` | Low, for faithful minutes (0 to 1) |
| `SUMMARIES_TIMEOUT_SECONDS` | `300` | Seconds without data before a step times out (> 0) |
| `SUMMARIES_TOTAL_TIMEOUT_SECONDS` | `3600` | Bound for one whole summary (> 0) |
| `SUMMARIES_MAX_RETRIES` | `2` | Retries for transient failures (>= 0) |

All numeric settings are validated (`gt`/`ge`); an invalid value fails at load, like `RECORDING_*`.

### 8a. The cloud API key (GNOME Keyring)

Owner decision of 2026-10-11, recorded in [ADR-009](../decisions/009-cloud-summaries-through-opencode-go.md).

- **Where**: the freedesktop Secret Service (`org.freedesktop.secrets`) of the user's session; no `.env`, settings or
  meeting file ever holds the key. No `SUMMARIES_CLOUD_API_KEY` setting exists.
- **Port**: `CloudKeyStore.get_key() -> Result[SecretStr, SummaryError]` in `summaries/domain`; the adapter
  `SecretServiceKeyStore` (`dbus-fast`, no new dependency) is in `summaries/infrastructure`. The cloud provider asks the
  store when a confirmed cloud request starts and holds the `SecretStr` for that request only.
- **Protocol** (Secret Service API 0.2): `OpenSession("plain", "")`, `SearchItems({application: meeting-scribe,
  key: opencode-api-key})`, then `GetSecrets(items, session)` on the unlocked match. A match only in the `locked` list
  gives `SUMMARY_CLOUD_KEY_LOCKED`; `Unlock` is never called (it prompts). The "plain" session is used because the
  secret crosses only the user's local session-bus socket.
- **Invariants**: never logged, never in an error message, `meeting.json`, a D-Bus reply, a command line or a worker
  environment; sent only over HTTPS to the configured base URL; in memory only for the request.
- **Limitation**: while the session is unlocked, any process running as the user can query the keyring (no per-app ACL
  outside Flatpak).
- **Storing it**: `secret-tool store --label="Meeting Scribe OpenCode API key" application meeting-scribe key opencode-api-key`
  with the secret on stdin (runbook `store-cloud-api-key.md`). A "Set API key" password field in the review app is F5.6 scope.
- **Tests**: a fake store for the use cases; a fake Secret Service object on a private `dbus-daemon`. The real keyring is never touched.

### 9. Errors and stable codes

New classes derive from `MeetingScribeError`; each is registered in `error-codes-catalog.md` and mapped by
`to_dbus_error`. Messages name the next step and never quote content or secrets.

| `error_code` | Meaning | Next step in the message |
|---|---|---|
| `SUMMARY_NO_SPEECH` | The meeting has no utterances | Nothing to summarize |
| `SUMMARY_CLOUD_DISABLED` | Cloud requested while the configured provider is local (the code keeps its name; its meaning follows product decision 9) | Choose Cloud in Preferences |
| `SUMMARY_CLOUD_KEY_UNAVAILABLE` | The Secret Service is not reachable (no service, D-Bus error or timeout); the cause is logged by exception type only | Check that gnome-keyring is running |
| `SUMMARY_CLOUD_KEY_MISSING` | Cloud selected, no key in the keyring | Store the key with `secret-tool` (runbook `store-cloud-api-key.md`) |
| `SUMMARY_CLOUD_KEY_LOCKED` | The keyring holds the key but is locked | Unlock the login keyring and try again; the service never prompts |
| `SUMMARY_CLOUD_NOT_CONFIRMED` | Cloud requested without `cloud_confirmed` | Confirm in the app |
| `SUMMARY_ALREADY_RUNNING` / `SUMMARY_BUSY` | A summary is running for this meeting / another meeting | Wait or cancel |
| `SUMMARY_GPU_BUSY` | Local summary while recording or processing | Try again when it ends |
| `SUMMARY_PROVIDER_UNAVAILABLE` | Provider unreachable (Ollama stopped, no network) | Start Ollama or check the network |
| `SUMMARY_MODEL_NOT_FOUND` | The configured model is not available | `ollama pull <model>` or fix the name |
| `SUMMARY_AUTH_FAILED` | 401 or 403 | Check the key |
| `SUMMARY_RATE_LIMITED` | 429 after the retries | Try again later |
| `SUMMARY_PROVIDER_REJECTED` | Another 4xx (HTTP status only) | Check the provider settings |
| `SUMMARY_TIMEOUT` | Step or total timeout | Retry; raise the timeout |
| `SUMMARY_CONTEXT_TOO_LARGE` | Truncation guard, or no room or budget for the transcript | Raise the context and the matching setting |
| `SUMMARY_OUTPUT_LIMIT_REACHED` | The model used the whole output limit (often hidden reasoning) | Raise `SUMMARIES_MAX_OUTPUT_TOKENS` or choose another model |
| `SUMMARY_OUTPUT_INVALID` | Empty or malformed model output after one repair | Retry or use another model |
| `SUMMARY_CANCELLED` | Cancelled by the user or by a recording start | Press Generate again |
| `SUMMARY_STORAGE_FAILED` | `summary.md` or `meeting.json` could not be written | Check disk space and permissions |
| `ENTITY_NOT_FOUND_SUMMARY` | No summary for the meeting (detail code of the existing entity-not-found class) | Generate one |

Reused: `MEETING_NOT_PROCESSED`, `ENTITY_NOT_FOUND_MEETING`, `MEETING_SCHEMA_UNSUPPORTED`, `MEETING_CORRUPT`,
`MEETING_STORAGE_FAILED`, `INVALID_VALUE`.

### 10. D-Bus: `dev.meetingscribe.Summaries1`

**Recommendation: a new interface `Summaries1` (it is already planned in `ARCHITECTURE.md` and `api-contracts.md`),
implemented by an adapter in `meetings/infrastructure/dbus/`, not in `summaries/`.** The reason is the one the review
spec gave for `Meetings1`: the adapter needs `meeting.json`, which belongs to `meetings`, and domains must not cross
(CR-001, CR-003). A separate interface (not more members on `Meetings1`) keeps `Meetings1` from growing without
bound and lets old clients ignore the new interface. Same bus name and object path. `api-contracts.md` is updated
first, in the same commit as the code (it replaces the "planned" row for `Summaries1`).

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `Providers` | method | `() -> aa{sv}` | One row per configured provider: `id` (s, `local` or `cloud`), `label` (s, e.g. `Local (<model>)`, `cloud: OpenCode Go, deepseek-v4-pro`; always contains the word "cloud"), `cloud` (b), `model` (s), `available` (b), `reason` (s, empty or an error code such as `SUMMARY_CLOUD_DISABLED`), `default` (b, true for the one row chosen by `SUMMARIES_PROVIDER`; the app's single button uses it). Local availability is not probed (no network call); it is learned when a request runs. The cloud URL and the key are never returned | none |
| `Summarize` | method | `(ssb) -> s` | `(meeting_id, provider_id, cloud_confirmed)`: start a summary and return `RUNNING` at once. Replaces an existing summary only on success | `MEETING_NOT_PROCESSED`; `SUMMARY_NO_SPEECH`; `SUMMARY_CLOUD_DISABLED`; `SUMMARY_CLOUD_KEY_MISSING`; `SUMMARY_CLOUD_NOT_CONFIRMED`; `SUMMARY_GPU_BUSY`; `SUMMARY_ALREADY_RUNNING`; `SUMMARY_BUSY`; `INVALID_VALUE`; the lookup errors |
| `CancelSummary` | method | `(s) -> b` | Cancel the running summary of the meeting; `true` if one was running, `false` otherwise (not an error) | `ENTITY_NOT_FOUND_MEETING` |
| `GetSummaryStatus` | method | `(s) -> a{sv}` | No content. `state` (s: `NONE`, `RUNNING`, `DONE`, `FAILED`), `stale` (b), `cloud` (b), `provider_label` (s), `generated_at` (s), `step` (i), `total_steps` (i), `failure_code` (s, last failure, empty when none). `FAILED` means the last attempt failed; a previous `summary.md` may still exist (`generated_at` non-empty) | the lookup errors |
| `GetSummary` | method | `(s) -> a{sv}` | The text and facts: `markdown` (s), `title` (s), `generated_at` (s), `provider_label` (s), `cloud` (b), `model` (s), `language` (s), `stale` (b). No path | `ENTITY_NOT_FOUND_SUMMARY`; the lookup errors |
| `LastSummary` | method | `() -> a{sv}` | The newest summary by `generated_at`: `meeting_id` (s), `started_at` (s, the meeting's start), `generated_at` (s), `path` (s, absolute). For the panel menu, which opens the file with the system handler and never builds a path | `ENTITY_NOT_FOUND_SUMMARY`; `MEETING_STORAGE_FAILED` |
| `ActiveSummaryMeetingId` | property | `s` (read) | The meeting being summarized; empty when idle | n/a |
| `SummaryProgress` | signal | `(sii)` | `(meeting_id, step, total_steps)` after each step, throttled to one per second | n/a |
| `SummaryFinished` | signal | `(sss)` | `(meeting_id, state, error_code)` when a summary ends, `state` `DONE`, `FAILED` or `CANCELLED`, emitted after the result is saved | n/a |

`PropertiesChanged` is emitted with `ActiveSummaryMeetingId`. Error messages carry ids and settings names, never
transcript text, names, summary text or keys. Clients refresh `GetSummaryStatus` on `SummaryFinished` and on
`SpeakersChanged` (a rename may make the summary stale).

**The menu action and the layout.** `LastSummary` returns the path as an opaque value: the extension passes it to the
default handler for Markdown (`Gio.AppInfo.launch_default_for_uri` on a `file://` URI built from the returned path, in
the Shell glue only, CR-012) and never concatenates a folder and a file name. When storage moves to the XDG layout
(backlog), the service returns the new path and no client changes. The menu label is built from `started_at` in
the user's locale (for example a weekday, day, month and 24-hour time), which is the "readable date" the owner asked for
instead of the folder id. The label carries no title or content (a title is generated from the transcript).
The extension calls `LastSummary` when the menu opens and on `SummaryFinished`; it treats `ENTITY_NOT_FOUND_SUMMARY`
and `UnknownMethod` as "hide the item" (an older service).

### 11. Output: `summary.md`

**The "reference structure"** referred to by the backlog is the output format in
[ADR-006](../decisions/006-summary-provider-openai-compatible.md) ("Output format"), which `PRD.md` FR-6 and the
F5 user story call "a fixed reference structure". It is defined there; this spec adopts it with one approved extension,
a Decisions section (product decision 1, [ADR-008](../decisions/008-summary-sections.md)):

```
# <Meeting title> - <date> (<time>, ~<N> min)
**Participants:** <name>, <name>, ...
**Generated by:** <provider label> / <model> on <date>
> Note: the transcript may contain recognition errors in names and technical terms.

## Topics
### 1. <topic>
- <point>

## Decisions
- <decision>

## Action items
| Owner | Task |
|---|---|
| <name> | <task> |

## Open questions
- <question>
```

- **Computed locally** (never by the model): the date, time, duration, Participants line, "Generated by" line and the
  note. **Written by the model**: the title (a single short line), Topics, Decisions, Action items, Open questions.
  This keeps names and facts exact and makes the header cacheable and testable.
- **Language** (product decision 4): body and headings follow the meeting language, `processing.detected_language` when set, else
  `TRANSCRIPTION_LANGUAGE`, unless `SUMMARIES_LANGUAGE` overrides. Headings come from a **gettext-style catalog**,
  not from literals in code: the English headings in the domain are the message ids, and
  `src/meeting_scribe/summaries/locale/<language>/LC_MESSAGES/meeting_scribe.po` maps them to a language (Spanish is
  shipped; English is the identity). The adapter `GettextHeadingCatalog` reads the `.po` source at run time with
  `importlib.resources` (no compile step, so it ships as plain package data; verified in the built wheel). A language
  without a catalog, or an unreadable catalog, gets English headings and labels with a body still written in the meeting
  language. To add a language, add one `.po` file and a test; no code changes. The model is told to copy the headings
  as shown, and output validation accepts only those headings.
  Action item owners use the speaker display names. Technical terms stay as spoken (section 3).
- Empty sections are kept with "None" in the language of the summary, so the structure is always the same.
- **Cloud and names** (product decision 3): names are sent to the cloud as written, like the rest of the transcript;
  the confirmation dialog says the transcript, with speaker names, will leave this computer.
- **Regeneration** (product decision 5): the previous `summary.md` is kept as `summary.previous.md` (one backup, replaced each
  time); a hand-edited file is therefore never lost by pressing Generate again.
- **Reprocess**: leaves `summary.md` and the `summary` section alone; the summary shows as out of date.
- **Atomic writes** with `os.replace` from a temporary file in the same folder; permissions as for `transcript.md`.

### 12. Task breakdown

Advisory size: about 400 authored lines per work unit (not a cap). Order is the work-unit order; each unit ships its
tests and docs. Branch per phase from `main`, one commit per unit, no push without asking.

| Task | Content | Rough size |
|---|---|---|
| F5.1 | `summaries` domain: `SummaryRequest`, ports, chunker, prompt builder (language and technical-terms rule) and heading table, renderer, fingerprint, errors, catalog and import contracts | ~350 |
| F5.2 | `GenerateSummary` use case: single pass and map-reduce, cancel, progress, validation with one repair, fake provider tests | ~300 |
| F5.3 | `SummariesSettings` (`SUMMARIES_PROVIDER`, loopback and https validation), the `CloudKeyStore` port with its GNOME Keyring adapter, `OpenAiCompatibleProvider` (streaming, retries, error mapping, truncation guard), `OllamaModelReleaser`, `openai` dependency, `.env.example`, `ARCHITECTURE.md` section 7; tests with an in-process fake transport | ~400 |
| F5.4 | `meetings`: `SummaryInfo` section, `SummaryController` (state, lock use, gates, one job thread, backup rotation, stale, `GpuLease` shared with `ProcessingController`), file store | ~400 |
| F5.5 | `Summaries1` adapter and wiring in `main.py`, `api-contracts.md` first, e2e on a private bus, signals | ~400 |
| F5.6a | Review app core: models, parsers, `SummariesGateway`, summary and cloud-confirmation presenters, error messages and drift test | ~400 |
| F5.6b | Review app `ui`: Summary view, one button labelled from `Providers`, confirmation dialog, cloud badge, progress and cancel, stale banner, copy and "Save a copy" | ~350 |
| F5.7 | Extension: `LastSummary` client, "Open last summary (date)" item, open glue, notification, `gjs` tests | ~250 |
| F5.8 | `install.sh` Ollama and model check (warn only), install tests with a fake `ollama`, runbook `docs/runbooks/summaries.md` (Ollama, context, model choice, cloud key) | ~250 plus docs |
| F5.9 | Validation with the user's consent (a real local run and, only on an explicit go, one cloud run, both on a synthetic transcript), rules-registry entries, `mvp-scope.md` FR-6, backlog, README, docs gate | ~150 plus docs |

### 13. Test strategy

| Level | What | How |
|---|---|---|
| Domain (unit) | Chunker boundaries and budget, token estimate, prompt builder, heading table, renderer, fingerprint | Plain values; synthetic transcripts only |
| Use case (unit) | One pass, map-reduce levels, cancel, progress, repair request, release call | A scripted fake `SummaryProvider` that records every request (so tests assert what would be sent and that no request exceeds the budget) |
| Adapter (unit) | Request shape, header, streaming assembly, error mapping, retries, truncation guard, `<think>` stripping, release call | The client with an in-process fake transport (no socket). If the HTTP library has no mock transport, a stdlib server bound to `127.0.0.1` on an ephemeral port serves scripted responses |
| No-network guard | No test reaches a real provider | An autouse fixture makes any connection to a non-loopback address raise; the cloud URL in tests is a fake host the transport answers |
| Meetings (unit) | Controller gates, lock use, backup rotation, stale, failure recording, GPU lease, shutdown | In-memory repository, fake files, fake clock, fake `GpuLease` (CR-009) |
| D-Bus (e2e) | Every member, signature, error name and signal | Private `dbus-daemon` (GO-025), `dbus_fast` client, fake provider behind the controller |
| App core (unit) | Presenters, confirmation flow, error drift test | Fake gateway; parsers fed the unpacked replies of the real `Summaries1` on the private bus |
| App views | Import smoke on system Python | As for the review app; constructs no window |
| Install | Ollama present, absent, stopped, model missing; never fails | Fake `ollama` on `PATH` |
| Extension | Menu item, label from a date, hide on error, open glue | `gjs -m` tests with an injected opener |
| Manual with consent | A real local summary of a synthetic transcript; one cloud summary only on an explicit go | Nothing from it enters the repo, logs or docs |

No test sends a request to Ollama or a cloud provider, plays audio or reads `~/Documents/meetings/`.

---

## Library facts verified (Context7, 2026-10-11)

| Fact | Source (Context7 id) | Status |
|---|---|---|
| Ollama exposes an OpenAI-compatible `POST /v1/chat/completions` at `http://localhost:11434/v1/`; the OpenAI client needs an `api_key` that is ignored; `messages`, `temperature`, `max_tokens`, `stream`, `seed`, `stop`, `response_format` are supported | `/ollama/ollama` (`docs/api/openai-compatibility.mdx`) | Verified in docs |
| The OpenAI-compatible API has no way to set the context size; use a Modelfile `PARAMETER num_ctx` with `ollama create`, or `OLLAMA_CONTEXT_LENGTH` for the server; `num_ctx` is an option of the native API | `/ollama/ollama` (`openai-compatibility.mdx`, `faq.mdx`, `context-length.mdx`) | Verified in docs |
| Default context is 4k under 24 GiB VRAM, 32k at 24-48 GiB, 256k from 48 GiB; `ollama ps` shows the allocated context and the GPU/CPU split | `/ollama/ollama` (`context-length.mdx`) | Verified in docs (the installed version is unknown, A-4) |
| `keep_alive: 0` on `/api/generate` unloads a model immediately; `OLLAMA_KEEP_ALIVE` sets the server default | `/ollama/ollama` (`faq.mdx`) | Verified in docs; behavior not exercised (A-6) |
| OpenCode Go is an OpenAI-compatible gateway: `https://opencode.ai/zen/go/v1/chat/completions` with a Bearer key serves DeepSeek, GLM, Kimi, MiMo and LongCat; Grok and GPT use `/responses`, MiniMax uses `/messages` | <https://opencode.ai/docs/go>, <https://opencode.ai/docs/zen> (owner decision, ADR-009) | Taken from the provider docs; not exercised (A-1) |
| `openai` client: `base_url`, `api_key`, `timeout` (float or granular timeout object, default 10 minutes), `max_retries` (default 2; connection errors, 408, 409, 429, 5xx), `with_options` per request, exception classes `APIConnectionError`, `APITimeoutError`, `RateLimitError`, `APIStatusError` with `status_code`, custom `http_client` | `/openai/openai-python` (README) | Verified in docs; the package is not yet a dependency (A-5) |
| `ollama` is installed at `/usr/local/bin/ollama` and `systemctl is-active ollama` reports `active` (system unit; the user unit is inactive; no `ollama` rpm) | Local read-only checks | Verified on this machine; no request was sent to it |

**Not verified** (no network call to a provider was allowed): which models are pulled in Ollama; the installed
Ollama version and its actual default context; that closing a stream stops generation; that `keep_alive: 0` frees
VRAM; the gateway's 402 and 429 behavior, its privacy and retention terms, and the real context window of the
chosen cloud model.

---

## Assumptions to confirm

| ID | Assumption | Confirm in |
|---|---|---|
| A-1 | The cloud model id `deepseek-v4-pro` is served by OpenCode Go on `/chat/completions` and accepts a plain chat request without the optional thinking parameters. The model name stays a setting | F5.9, one cloud run on an explicit go; check the provider's model list before release |
| A-2 | The cloud context budget of 100000 tokens is safe for the chosen model (published figures vary by model) | F5.9 |
| A-3 | An 8B-class model at Q4 plus an 8192-token context fits in about 6 GB of the 8 GB card shared with the desktop (the default model `qwen3.5:9b` is 6.6 GB on disk on the owner's machine, so a short context is the safe choice and the default stays 4096; rough arithmetic for a typical 8B model: about 5 GB of weights plus about 1 GB of key-value cache for a typical 8B model with grouped-query attention at 8k, which is an estimate, not a measurement) | F5.9 with `ollama ps` on a synthetic transcript, on the user's go; if it does not fit, keep 4096 or pick a smaller model |
| A-4 | The installed Ollama defaults to 4096 tokens on this GPU (documented default under 24 GiB) | F5.9 (`ollama ps`, CONTEXT column) |
| A-5 | `openai` installs on Python 3.12 without heavy dependencies and without conflicting with the lock; otherwise use `httpx2` directly | F5.3 (`uv add openai`, `make check`) |
| A-6 | Closing the stream aborts generation in Ollama, and `keep_alive: 0` frees the VRAM before the next Whisper load | F5.9 on the user's go (a stop-and-measure with `nvidia-smi` is the same kind of check as GO-037) |
| A-7 | The token estimate of 3 characters per token is conservative enough for Spanish and English; the truncation guard catches a miss | F5.2 (unit) and F5.9 |
| A-8 | The default handler for Markdown exists in the user's session; if not, the open fails with one notification | F5.7 (glue is checked statically, GO-031) and the owner's manual check |
| A-9 | The Shell glue can open a file URI from the service with `Gio.AppInfo.launch_default_for_uri` without blocking | F5.7 |
| A-10 | A summary of a typical 1-hour meeting finishes in a few minutes locally and in under a minute on the cloud | F5.9 (informs the default timeouts) |

---

## Product decisions

The owner answered all eight questions on 2026-10-11.

1. **OQ-1** Sections: **add a Decisions section** (option b). Recorded in [ADR-008](../decisions/008-summary-sections.md);
   ADR-006 is not edited.
2. **OQ-2** Start: **manual only** (option b). The "Generate summary" button is the only trigger; nothing is automatic.
3. **OQ-3** Names in the cloud: **send speaker names as written** (option a). There is no placeholder mechanism; the
   confirmation dialog states that names leave the computer with the transcript.
4. **OQ-4** Language: **the meeting language**, overridable by `SUMMARIES_LANGUAGE`, plus: **technical terms are never
   translated** (kept as spoken, for example "deploy", "pull request"). It is part of the prompt contract (section 3)
   and AC-22.
   **Refined 2026-10-11**: the summary (headings and body) is written in the language detected for the meeting, the
   `SUMMARIES_LANGUAGE` override still wins; headings come from a gettext-style catalog with Spanish shipped and English
   as the fallback for any other language (AC-23). No Spanish text in code, only in the catalog file.
5. **OQ-5** Regenerate and Reprocess: **regenerate overwrites, keeping one backup** (`summary.previous.md`);
   **Reprocess keeps the summary and marks it out of date** (option b).
6. **OQ-6** Default local model: **`qwen3.5:9b`**, already installed on the owner's machine (6.6 GB, fits the 8 GB
   RTX 5060 with a short context). `make install` only checks it exists and never pulls it.
7. **OQ-7** Menu item: **"Open last summary" opens `summary.md` in the default app for `.md` files** (option a; on the
   owner's machine, Sublime Text).
8. **OQ-8** Choosing the provider: **one "Generate summary" button that uses the provider set in the settings**
   (option a, `SUMMARIES_PROVIDER`). Safety requirement 3 still applies: when the configured provider is the cloud, the
   caption under the button carries "Cloud" and the model (F5.9) and every cloud
   request asks for confirmation before sending (`cloud_confirmed` stays). There is no separate per-request cloud entry.
9. **Decision 9 (2026-10-11, owner)** Cloud opt-in: **there is no separate "Enable cloud" switch.** Choosing the cloud as the
   provider (`SUMMARIES_PROVIDER=cloud`, set from the Preferences window) IS the opt-in, and `SUMMARIES_CLOUD_ENABLED` is
   removed as a separate concept (migration in [`preferences.md`](preferences.md) section 7). Safety requirement 3 is
   unchanged: the cloud is labelled "cloud" everywhere it can be triggered and every cloud request still asks for
   confirmation (`cloud_confirmed`). The cloud fields of the Preferences window appear only when Cloud is selected.
10. **Decision 10 (2026-10-11, owner)** Labels and model pickers: the Preferences selector for `SUMMARIES_PROVIDER` reads
   "Run summaries: On this computer / In the cloud" (the setting keeps its name); "Provider" in the Cloud group means the
   cloud provider (`SUMMARIES_CLOUD_PROVIDER`, "OpenCode Go"); models are never typed but picked from lists the service
   reads (Ollama `/api/tags`, the provider's `/models` behind the keyring key); models served on endpoints this version
   does not call are listed as "not supported yet" ([`preferences.md`](preferences.md) AC-16 to AC-20, GO-062).

---

## Definition of Done

- [ ] All acceptance criteria pass in `make check`
- [ ] The `Summaries1` interface is documented in `architecture/api-contracts.md` before the code (F5.5)
- [ ] New error codes registered in `architecture/error-codes-catalog.md`
- [ ] New settings in `.env.example` and `ARCHITECTURE.md` section 7; new import contracts listed in `ARCHITECTURE.md`
- [ ] Unit tests written first (RED), then implementation (GREEN)
- [ ] Lint, format, type check and import contracts clean
- [ ] The cloud path is gated by setting, key, `cloud_confirmed` and a visible confirmation, and is labelled "cloud" in every UI that triggers it
- [ ] The API key and transcript content appear in no log, error, fixture, commit or doc (checked by the tests in AC-9 and AC-7)
- [ ] `make install` reports Ollama and the model without failing; `make uninstall` leaves meetings and `.env`
- [ ] `docs/runbooks/summaries.md` written (Ollama, context, model choice, cloud key, troubleshooting)
- [ ] Traceability for FR-6 filled in `project/mvp-scope.md`; `README.md`, `ARCHITECTURE.md` and the backlog updated
- [ ] New gotchas and rules in `project/rules-registry.md` (Ollama context, truncation guard, GPU release, key handling)
- [ ] Manual validation with the user's consent: a real local summary and, on an explicit go, one cloud summary, both on a synthetic transcript
- [x] Product questions answered by the user (2026-10-11)
- [ ] Docs gate before the integration PR is reported ready (`project/doc-sync-protocol.md`)

---

## Decisions taken during implementation

> If unresolved forks appear, record them here AND open an ADR if the decision is structural.

- **F5.3 (2026-10-11)**: the `openai` client (3.x) is built on `httpx2`, not `httpx`; tests pass an
  `httpx2.MockTransport` through `http_client`, and `httpx2` is declared as a direct dependency because the adapter
  and the releaser import it. The import-linter contract confines `openai` and `httpx2` to
  `summaries.infrastructure` (contract 14).
- **F5.3**: an empty answer is returned by the adapter as an empty `Completion` and judged by `GenerateSummary`
  (`SUMMARY_OUTPUT_INVALID`), so the rule lives in one place. A stream that ends without a `finish_reason` is a
  retryable provider failure. The truncation guard applies to the local provider only; `finish_reason` `length`
  applies to both.
- **F5.3**: `SUMMARIES_CLOUD_PROVIDER_NAME` (default `OpenCode Go`) is a setting so the service builds the label
  `cloud: <name>, <model>`. `SUMMARIES_LOCAL_CONTEXT_TOKENS` and `SUMMARIES_CLOUD_CONTEXT_TOKENS` must exceed
  `SUMMARIES_MAX_OUTPUT_TOKENS` plus 600.
- **F5.3**: a connect or first-byte wait cannot be interrupted by a cancel (no stream exists yet); it is bounded by
  the connect timeout (5 s) and the read timeout capped by the time left.
- **F5.9 (2026-10-12)**: the button says just "Generate summary"; a small caption under it names where it runs and
  the model ("Cloud · <model>" or "On this computer · <model>"). The Summary tab no longer has a "Set API key"
  button: a missing key shows "Set the API key in Preferences" with a button that opens Preferences. A provider or
  model change (`SettingsChanged`), or reopening the tab, clears the error of an earlier run; the service keeps the
  last failure in `meeting.json`, and the app shows it only for a run that finished while the window was open.
- **F5.6 (2026-10-11)**: the button label is `Generate summary: <provider label>` with the label exactly as `Providers`
  words it (the cloud label already says "cloud"); the colon avoids nested parentheses with `Local (<model>)`. The
  "Set API key" field of section 8a is built in the app (`ApiKeyPresenter` in `core`, `SecretKeyStore` on libsecret in
  `ui`): it is offered only when the configured provider is the cloud and available, stores the item that `secret-tool`
  makes, and the key is never kept in a state nor sent to the service. The summary text is shown as plain selectable
  text (no Markdown rendering); a `FAILED` run with a previous `summary.md` shows the failure and keeps the old text.
