---
type: decision
adr_number: 010
status: active  # proposed | active | superseded | deprecated
date: 2026-10-12
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Draft: it becomes active when the owner approves docs/specs/install.md."
  - "No script in this repo may run sudo or prompt. Printing a sudo command for a human to run is allowed."
---

# ADR-010: The install checks first, never uses sudo and never prompts

## Status

`active` (approved 2026-10-12) - drafted with [`../specs/install.md`](../specs/install.md) on 2026-10-12, from the owner's decisions of the same day.

## Context

`scripts/install.sh` today installs first and warns about gaps afterwards (toolkit, Ollama, models), so a machine
can end up half installed. System packages, the NVIDIA driver and Ollama need privileges the installer should not
hold: a script that runs `sudo` hides privileged commands, cannot be reviewed by the user, and cannot be driven by an
AI agent without handing it root. A prompt in the middle of an install breaks non-interactive and agent use.

## Options Considered

### Option A: Installer runs `sudo dnf install` for what is missing

**Pros**: one command, fewer steps.
**Cons**: hidden privileged commands, a mid-way password prompt, hard to test, unsafe for agents.
**Cost**: medium code, high trust cost.

### Option B: Install first, warn afterwards (today)

**Pros**: no new code.
**Cons**: half-installed systems; prerequisites reported after the changes.
**Cost**: none.

### Option C: Read-only preflight, stop with zero changes, print the commands

**Pros**: nothing changes until everything is present; every fix command is visible; works for humans and agents; testable with fakes.
**Cons**: the user runs the printed commands and re-runs the installer.
**Cost**: one preflight script and its tests.

---

## Decision

**We choose Option C.**

`./install.sh` runs a read-only preflight before any change. When anything is missing it stops with zero changes and prints,
for each item, what is missing, why it is needed and the exact fix command. `./install.sh --check` runs only the preflight.
The installer never runs `sudo` and never prompts; it prints commands. Steps only the user can do (the NVIDIA driver and
reboot, accepting the pyannote terms and creating the Hugging Face token) are labelled `HUMAN`, distinct from fixable
`FIX` items. Exit codes (0, 1, 2, 10, 11) and a `--json` report are a stable contract.

---

## Consequences

### Positive

- No half-installed machine; every privileged command is read by the user first.
- An AI agent can drive it safely: it fixes `FIX` items with the user's consent and hands `HUMAN` items back.

### Negative

- Two or more runs when prerequisites are missing.

### Accepted trade-offs

- Fix commands target Fedora (`dnf`); other distributions read the package names.

### Trigger to revisit

- A distribution channel with its own package manager (RPM or COPR), where dependencies are declared instead of checked.

---

## Implementation notes

- Affected: `install.sh` (root wrapper), `scripts/install.sh`, `scripts/preflight.sh`, `scripts/uninstall.sh`, `Makefile`, `README.md`, `docs/runbooks/install.md`.
- Tests: `tests/unit/test_preflight.py`, `tests/unit/test_install_scripts.py`, `tests/unit/test_readme_prerequisites.py`.
- Future note: a later extensions.gnome.org extension may not ship binaries or run privileged installs (review guidelines, gjs.guide, as stated by the owner); it could reuse `./install.sh --check --json` as the user-run readiness step.

---

## References

- [`../specs/install.md`](../specs/install.md) sections 3 to 5
