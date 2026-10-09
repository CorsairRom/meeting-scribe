# meeting-scribe

A local, GPU-accelerated meeting assistant for GNOME (Fedora, Wayland). Record from a panel button,
get a transcript with speakers told apart, name or merge speakers in a native GNOME review app, and generate a summary
with a local (Ollama) or opt-in cloud (DeepSeek) LLM. Everything runs on your machine; nothing leaves it
unless you explicitly ask for a cloud summary.

**Status**: F0 (foundations) — scaffold, tooling and docs. No recording feature yet; see the [scope](docs/project/mvp-scope.md).

## Quickstart

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
make setup   # install runtime + dev dependencies
make check   # ruff, pyrefly, import-linter contracts, pytest
make run     # start the service (no transport yet; D-Bus arrives in F1)
```

Copy `.env.example` to `~/.config/meeting-scribe/.env` (or `$XDG_CONFIG_HOME/meeting-scribe/.env`) to override
settings (`LOG_LEVEL`, `STORAGE_MEETINGS_DIR`); a `./.env` in the project directory is the development fallback.
Run `make help` to list all targets.

## Documentation

| Doc | What it is |
|---|---|
| [`PRD.md`](PRD.md) | Problem, goals, requirements, safety rules |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System context, bounded contexts, layering, GPU policy, configuration |
| [`AGENTS.md`](AGENTS.md) | Conventions and workflow for AI agents and contributors (`CLAUDE.md` links to it) |
| [`docs/`](docs/README.md) | Taxonomy: project scope and rules, architecture, ADRs, specs, runbooks, ideas |

## Safety

The service never starts a recording or plays audio on its own, and meeting data is private: it stays in the
configured meetings directory and never enters this repository.
