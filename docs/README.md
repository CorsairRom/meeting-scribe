# Documentation — meeting-scribe

Entry point for humans and AIs. Before creating, editing or searching a document, read the taxonomy
to know exactly where each thing goes.

Product vision lives in [`../PRD.md`](../PRD.md); system overview in [`../ARCHITECTURE.md`](../ARCHITECTURE.md);
agent rules in [`../AGENTS.md`](../AGENTS.md).

---

## Taxonomy — one question per folder

For every new document, ask yourself ONE question:

| Question | Folder |
|---|---|
| What are we building and which rules govern the project? | `project/` |
| How is the system built today (real state)? | `architecture/` |
| Why was a technical or product decision taken? | `decisions/` |
| What exactly must this feature do (expected behavior)? | `specs/` |
| How do I run this operation step by step? | `runbooks/` |
| What idea came up that has no scope yet? | `ideas/` |
| What do I copy to start a new doc of a given kind? | `_templates/` |

**Golden rule**: if a doc answers two questions, it is in the wrong place or must be split.

---

## Folders

### `project/` — the project contract

Documents that define **what** we build and **under which rules**. Read these first when joining.

| File | Purpose |
|---|---|
| [`mvp-scope.md`](project/mvp-scope.md) | Exact list of what is IN/OUT of scope, per phase, with traceability. Source of truth for scope. |
| [`backlog.md`](project/backlog.md) | Pending, in-progress and completed work, organized by epic. **Start here to see what is left.** |
| [`rules-registry.md`](project/rules-registry.md) | Living registry of business (BR), code (CR), gotcha (GO) and anti-pattern (AP) rules. |
| [`doc-sync-protocol.md`](project/doc-sync-protocol.md) | When and how to update these docs. Mandatory on every commit. |

> **AI**: read `mvp-scope.md`, `backlog.md` and `rules-registry.md` before implementing any feature.

### `architecture/` — the system as it is today

Descriptive documents of the **current** system. They reflect real code, not a future target.
If the code changes, these docs change in the same commit. The system-level overview is the root
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

| File | Purpose |
|---|---|
| [`api-contracts.md`](architecture/api-contracts.md) | D-Bus interfaces: methods, properties, signals and their errors. |
| [`error-codes-catalog.md`](architecture/error-codes-catalog.md) | Error response format and every stable `error_code`. |

Planned (created with the phase that introduces them): `data-model.md` (deferred to F4).
Copy from [`_templates/architecture/`](_templates/architecture/).

> **AI**: before touching a D-Bus interface or entity, read the matching file first.

### `decisions/` — why something was decided (ADRs)

Architecture decision records: context, options considered, decision. **Never deleted** — if a decision
changes, mark it `superseded` and create a new ADR.

| File | Decision | Status |
|---|---|---|
| [`001`](decisions/001-thin-gnome-extension-dbus-service.md) | Thin GNOME extension + local Python service over the D-Bus session bus | active |
| [`002`](decisions/002-separate-mic-and-system-audio-tracks.md) | Separate mic and system audio tracks (2-channel chunks) | active |
| [`003`](decisions/003-process-after-stop-single-gpu-worker.md) | Process after stop, single GPU worker, no live transcription | active |
| [`004`](decisions/004-folder-per-meeting-json-storage.md) | One folder per meeting with versioned `meeting.json` | active |
| [`005`](decisions/005-review-ui-native-gtk-app.md) | Review UI as a native GTK4 + libadwaita app | active |
| [`006`](decisions/006-summary-provider-openai-compatible.md) | `SummaryProvider` port with an OpenAI-compatible adapter | active |
| [`007`](decisions/007-gpu-worker-subprocess.md) | GPU work in short-lived worker processes, one per GPU stage | active |
| [`008`](decisions/008-summary-sections.md) | The summary has a Decisions section (extends ADR-006's format) | active |
| [`009`](decisions/009-cloud-summaries-through-opencode-go.md) | Cloud summaries go through OpenCode Go (replaces ADR-006's DeepSeek preset) | active |
| [`010`](decisions/010-install-never-uses-sudo.md) | The install checks first, never uses sudo and never prompts | active |
| [`011`](decisions/011-hugging-face-token-in-keyring-and-models-from-preferences.md) | The Hugging Face token lives in the keyring; models are downloaded from Preferences | active |
| [`012`](decisions/012-cross-desktop-tray-in-standalone-gjs.md) | A cross-desktop tray in standalone GJS reuses the extension's modules | active |

> **AI**: before taking a technical decision, check whether an ADR already covers it.
> If an ADR is `superseded`, follow the one that replaces it.

### `specs/` — what each feature must do

Behavior specifications: use cases, feature business rules, Definition of Done. More detailed than an ADR,
narrower than an architecture doc. See [`specs/README.md`](specs/README.md).

### `runbooks/` — how to run operations

Step-by-step instructions with exact commands, preconditions and what to verify.
See [`runbooks/README.md`](runbooks/README.md).

### `ideas/` — ideas without scope

Ideas with no decision yet. When an idea becomes an ADR it is marked approved and closed.
See [`ideas/README.md`](ideas/README.md).

### `_templates/` — copy to start a doc

Templates for every doc kind above. See [`_templates/README.md`](_templates/README.md).

---

## Flow for a new doc

```
Is it a shapeless idea?
  -> ideas/README.md

Did you take a technical or product decision?
  -> decisions/NNN-kebab-name.md  (new ADR, copy _templates/decisions/_adr-template.md)

Are you describing how a feature must behave?
  -> specs/name.md  (copy _templates/specs/_feature-template.md)

Are you documenting how the system is built?
  -> architecture/name.md  (or the root ARCHITECTURE.md for system-level views)

Is it a business rule, code rule, gotcha or anti-pattern?
  -> project/rules-registry.md  (new BR/CR/GO/AP entry)

Is it an operational step-by-step?
  -> runbooks/name.md
```

---

## Maintenance rules

- **Same commit as the code**: change a D-Bus method, property or signal, update its contract in the same commit.
- **ADRs are never deleted**: mark `superseded` and reference the new one.
- **`rules-registry.md` is living**: every non-trivial decision creates a new entry.
- **`mvp-scope.md` reflects real state**: when a deliverable is done, mark it with date and commit hash.
- **Frontmatter**: every doc starts with YAML (`type`, `status`, `last_updated`); bump `last_updated` on edits.
- **Language**: English, neutral professional tone.
- **Privacy**: never put real meeting content, names or topics in any doc. Use placeholders.

See [`project/doc-sync-protocol.md`](project/doc-sync-protocol.md) for the full protocol.
