# AGENTS.md

## What this project is

**meeting-scribe** — a local, GPU-accelerated meeting assistant for GNOME (Fedora, Wayland). A Python service
(`systemd --user`, D-Bus session bus API) records meetings, transcribes (faster-whisper) and diarizes (pyannote)
after stop, and summarizes through an OpenAI-compatible LLM (Ollama local / OpenCode Go cloud opt-in). A thin GNOME
Shell extension is the recording trigger, and a native GTK4/libadwaita app (opened from the extension menu) lets the
user name/merge speakers. Both are thin D-Bus clients. Python 3.12, `uv`, strict layered domains enforced by
import-linter, errors as `returns.Result`.

## Core conventions (NEVER violate)

**Safety (non-negotiable)**

1. **Never play audio or start a recording without asking the user first.** A past test recorded a live meeting. Use synthetic WAV fixtures; tests never touch audio devices.
2. **Meeting audio, transcripts and names are private.** Never put real meeting content in the repo, fixtures, docs, logs or commit messages. Never open the meetings directory (`~/Documents/meetings/`). Never print or read the Hugging Face token or `.env`.
3. **A cloud LLM provider is opt-in** and visibly labelled "cloud". Transcripts leave the machine only on an explicit user action.

**Architecture and code**

4. **Layered architecture** per package: `domain` -> `application` -> `infrastructure`. Domain has no framework imports (`pydantic_settings`, `dbus_fast`, `gi`).
5. **Domains do not cross.** `recording`, `transcription`, `speakers`, `summaries` never import each other. Only `meetings` depends on them, and only via their `application` layer. Enforced by `make imports`.
6. **Absolute imports only.** Never relative.
7. **Controllers return `Result[T, E]`** (`returns`). Never raise; return `Failure(<MeetingScribeError>)`.
8. **Repositories use the Null Object pattern.** Never `None`; convert with `BaseEntity.as_result(error)`.
9. **State transitions are entity methods** returning `model_copy(update=...)`. Entities are frozen. Pass `now` in.
10. **Settings are injected from the composition root.** Domains never import `meeting_scribe.settings` or `meeting_scribe.main`. Per-domain settings use `env_prefix`.
11. **Stable error codes.** Every error class has a UPPER_SNAKE `error_code`; register new ones in `docs/architecture/error-codes-catalog.md`.
12. **Conventional Commits**, lowercase, imperative. No `Co-Authored-By`, no AI attribution.

## Entities and policies

- Entities are pydantic v2 `BaseModel` subclasses of `BaseEntity` (`src/meeting_scribe/base/domain/entity.py`); a null variant sets the `_is_null_object = True` class marker (named `_Null<Entity>` by convention).
- Invariants an entity can check from its own state live on the entity; thresholds, cross-entity or user-dependent rules live in a separately named policy class in the domain.
- Ports (repositories, providers, capture, GPU model wrappers) are defined in `domain/`; adapters live in `infrastructure/`.
- Details and gotchas: `docs/project/rules-registry.md` (BR/CR/GO/AP).

## Where to look first

| You want to...                       | Read                                                           |
| ------------------------------------ | -------------------------------------------------------------- |
| Understand the product               | `PRD.md`                                                       |
| Understand the system and layering   | `ARCHITECTURE.md`                                              |
| Know what is in/out of scope         | `docs/project/mvp-scope.md`                                    |
| See what work remains                | `docs/project/backlog.md` and `odd/tasks/meeting-scribe.md`    |
| Check rules, gotchas, anti-patterns  | `docs/project/rules-registry.md`                               |
| Know why something was decided       | `docs/decisions/` (ADR 001-011)                                |
| Change the D-Bus API                 | `docs/architecture/api-contracts.md` + ADR-001        |
| Add an error code                    | `docs/architecture/error-codes-catalog.md` + `src/meeting_scribe/base/domain/errors.py` |
| Add a setting                        | `src/meeting_scribe/settings.py` + `.env.example` + `ARCHITECTURE.md` section 7 |
| Wire a new D-Bus interface or adapter | `src/meeting_scribe/main.py` (composition root)               |
| Change an import contract            | `pyproject.toml` `[tool.importlinter]`                         |
| Install, update or uninstall; fix a failed preflight | `docs/runbooks/install.md` + `docs/specs/install.md`  |
| Package or activate the service      | `packaging/` + `docs/runbooks/record-via-gdbus.md`             |
| Set up or debug processing and models | `docs/runbooks/processing.md`                                 |
| Pack, try or install the extension   | `docs/runbooks/gnome-extension.md`                             |
| Install, open or debug the review app | `docs/runbooks/review-app.md`                                 |
| Set up or debug summaries (Ollama, cloud key) | `docs/runbooks/summaries.md`                          |
| Change settings (Preferences window, settings.json) | `docs/runbooks/preferences.md`                  |
| Write a doc                          | `docs/README.md` (taxonomy) and `docs/_templates/`             |
| Know when to update docs             | `docs/project/doc-sync-protocol.md`                            |
| See the canonical shared-kernel code | `src/meeting_scribe/base/`                                     |

## Workflow expectations

- **ODD (Organic Driven Development)**: for substantial work, keep `odd/tasks/<feature>.md` (checklist, evidence, route log) and check items off only after observing outcomes.
- **Strict TDD**: RED (observe failing test), GREEN, REFACTOR. Runner: `uv run pytest` via `make test`. Tests in `tests/{unit,e2e}/<domain>/`, fakes over mocks.
- **Work-unit commits**: each commit is one reviewable unit with its tests and docs. Branch per phase from `main` (for example `f1/feat-recording`).
- **No push, no PR, no merge without asking.**
- **Doc sync**: follow `docs/project/doc-sync-protocol.md`; update docs in the same commit as the code.
- **Docs gate**: before an integration PR is reported as ready to merge, `make check` is green and a read-only docs audit has no open P1/P2 and a language scan finds no Spanish outside chat (see "Integration PR docs gate" in `docs/project/doc-sync-protocol.md`).
- **Current phase**: F6 Install (in progress); the phase table in `docs/project/mvp-scope.md` is the source of truth and a drift test keeps this line in sync.
- **Bug fix**: do not refactor unrelated code or widen scope.

## Tooling

- **Package manager**: `uv` (Python ~=3.12). Dev tools are in the `quality-assurance` extra.
- **Make targets**: `make setup` (`uv sync --extra ml --extra quality-assurance`), `make lint` (ruff + pyrefly + import-linter), `make format` (auto-fix), `make imports` (architecture contracts), `make test` (`pytest -m 'not gpu'`; GPU tests only with `uv run pytest -m gpu`), `make test-extension` (extension logic and D-Bus client against a private `dbus-daemon`, `gjs -m`), `make test-packaging` (builds the wheel offline with uv and checks the shipped package data), `make check` (lint + test + test-packaging + test-extension), `./install.sh` / `./uninstall.sh` and their aliases `make install` / `make uninstall` (a read-only preflight, then the install of service, ML stack, review app and extension; `./install.sh --check` is read-only and always allowed, the rest runs only on the user's explicit go; the models are downloaded from Preferences, and `meeting-scribe prefetch-models` follows the same rule), `make run` (development only, foreground service), `make run-review-app` (opens the review app window from the working tree; only on the user's explicit go), `make extension-pack` (build the extension zip into `dist/`), `make extension-install` (install it, does not enable).
- **Linter/formatter**: ruff (`ruff.toml`, line length 100). **Type checker**: pyrefly (`pyrefly.toml`).
- **Search/read tools**: prefer `rg`, `fd`, `bat`, `eza` over `grep`, `find`, `cat`, `ls`.

## What NOT to do

- Don't start a recording, play audio, or run anything that opens an audio device without explicit permission.
- Don't read, copy or reference real meeting data (audio, transcripts, summaries, names, topics).
- Don't run ML inside gnome-shell; the extension is a thin D-Bus client.
- Don't add an HTTP server; every client talks to the service over the D-Bus session bus (ADR-001).
- Don't import `gi` or `dbus_fast` from `domain/`; the GTK review app runs on system Python, with presenters free of `gi`.
- Don't import across domains or from `domain/` into frameworks; run `make imports`.
- Don't raise from controllers; don't return `None` from repositories.
- Don't use relative imports or read settings inside a domain.
- Don't use `pkill -f`; kill by PID. Don't print the Hugging Face token.
- Don't keep Whisper and pyannote resident together on the GPU.
- Don't add features, abstractions or refactors beyond the task. Don't write half-finished code; ask.
- Don't commit secrets (`.env`), audio, or cache files (see `.gitignore`).
- Don't add `Co-Authored-By` or AI attribution to commits.
- Don't use emoji in docs headings; docs are English, neutral professional tone.
