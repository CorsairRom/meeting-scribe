"""Drift tests: fail the build when the docs stop matching the code.

Plain file reading plus ``ast``/regex, no extra dependencies. Every failure message names the file
to fix. The protocol is ``docs/project/doc-sync-protocol.md``.
"""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meeting_scribe"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "odd",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
}

PHASE_STATUSES = {"done", "in progress", "pending"}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not EXCLUDED_DIRS.intersection(path.relative_to(ROOT).parts)
    )


def _error_codes_in_code() -> set[str]:
    """``error_code = "X"`` class attributes plus the ``WORKER_*`` codes the worker emits."""
    codes: set[str] = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign | ast.Assign):
                targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                value = node.value
                is_error_code = any(
                    isinstance(t, ast.Name) and t.id == "error_code" for t in targets
                )
                if (
                    is_error_code
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    codes.add(value.value)
            if (
                path.name == "worker.py"
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.fullmatch(r"WORKER_[A-Z_]+", node.value)
            ):
                codes.add(node.value)
    return codes


def test_every_error_code_is_in_the_catalog() -> None:
    catalog = _read("docs/architecture/error-codes-catalog.md")
    missing = sorted(code for code in _error_codes_in_code() if f"`{code}`" not in catalog)
    assert not missing, (
        "docs/architecture/error-codes-catalog.md does not list these error codes "
        f"defined in src/: {missing}"
    )


def _adrs() -> dict[str, tuple[str, str]]:
    """ADR number -> (file name, status from the frontmatter)."""
    adrs: dict[str, tuple[str, str]] = {}
    for path in sorted((ROOT / "docs" / "decisions").glob("[0-9][0-9][0-9]-*.md")):
        match = re.search(r"^status:\s*(\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert match, f"{path.relative_to(ROOT)} has no `status:` in its frontmatter"
        adrs[path.name[:3]] = (path.name, match.group(1))
    return adrs


def test_adr_table_lists_every_adr_with_its_status() -> None:
    readme = _read("docs/README.md")
    problems: list[str] = []
    for number, (name, status) in _adrs().items():
        row = next((line for line in readme.splitlines() if f"decisions/{name}" in line), None)
        if row is None:
            problems.append(f"ADR {number} is missing")
        elif not row.rstrip().endswith(f"| {status} |"):
            problems.append(f"ADR {number} should have status `{status}`")
    assert not problems, f"docs/README.md ADR table is out of date: {problems}"


def test_agents_states_the_latest_adr_range() -> None:
    numbers = sorted(_adrs())
    expected = f"ADR 001-{numbers[-1]}"
    assert expected in _read("AGENTS.md"), (
        f"AGENTS.md must mention `{expected}` (highest file in docs/decisions/)"
    )


def _contract_names() -> list[str]:
    config = tomllib.loads(_read("pyproject.toml"))
    contracts = config["tool"]["importlinter"]["contracts"]
    return [contract["name"] for contract in contracts]


def test_architecture_lists_every_import_contract() -> None:
    architecture = _read("ARCHITECTURE.md")
    names = _contract_names()
    missing = [name for name in names if name not in architecture]
    assert not missing, f"ARCHITECTURE.md (section 3, Enforced contracts) does not list: {missing}"
    section = architecture.split("### Enforced contracts", 1)[1].split("\n---", 1)[0]
    rows = re.findall(r"^\|\s*(\d+)\s*\|", section, re.MULTILINE)
    assert len(rows) == len(names), (
        f"ARCHITECTURE.md lists {len(rows)} contracts but pyproject.toml defines {len(names)}"
    )


def _is_settings_class(node: ast.ClassDef) -> bool:
    return any(
        isinstance(base, ast.Name) and base.id in {"BaseSettings", "MeetingScribeSettings"}
        for base in node.bases
    )


def _env_prefix(node: ast.ClassDef) -> str:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            for keyword in getattr(stmt.value, "keywords", []):
                if keyword.arg == "env_prefix" and isinstance(keyword.value, ast.Constant):
                    return str(keyword.value.value)
    return ""


def _is_nested_settings(stmt: ast.AnnAssign) -> bool:
    """``x: FooSettings = Field(default_factory=FooSettings)`` composes another class."""
    value = stmt.value
    return isinstance(value, ast.Call) and any(k.arg == "default_factory" for k in value.keywords)


def _env_vars_in_code() -> set[str]:
    variables: set[str] = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and _is_settings_class(node)):
                continue
            prefix = _env_prefix(node)
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and not stmt.target.id.startswith("_")
                    and not _is_nested_settings(stmt)
                ):
                    variables.add(f"{prefix}{stmt.target.id}".upper())
    return variables


def test_every_setting_is_in_env_example_and_architecture() -> None:
    variables = _env_vars_in_code()
    assert variables, "no settings found in src/: the drift test itself is broken"
    env_example = _read(".env.example")
    architecture = _read("ARCHITECTURE.md")
    absent_from_env = sorted(
        var for var in variables if not re.search(rf"^(#\s*)?{var}=", env_example, re.MULTILINE)
    )
    absent_from_architecture = sorted(var for var in variables if var not in architecture)
    assert not absent_from_env, f".env.example is missing: {absent_from_env}"
    assert not absent_from_architecture, (
        f"ARCHITECTURE.md (section 7, Configuration) is missing: {absent_from_architecture}"
    )


_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for path in _markdown_files():
        text = _INLINE_CODE.sub("", _FENCE.sub("", path.read_text(encoding="utf-8")))
        for target in _LINK.findall(text):
            if re.match(r"^([a-z][a-z0-9+.-]*:|#)", target):
                continue
            file_part = target.split("#", 1)[0]
            if file_part and not (path.parent / file_part).exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "Broken relative markdown links, fix the listed files: " + "; ".join(broken)


def _phase_rows() -> list[tuple[str, str]]:
    """(phase id, status) for each phase row of the scope table in mvp-scope.md."""
    rows: list[tuple[str, str]] = []
    for line in _read("docs/project/mvp-scope.md").splitlines():
        match = re.match(r"^\|\s*(F\d+(?:\.\d+)?)\b[^|]*\|.*\|\s*([^|]+?)\s*\|\s*$", line)
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def test_scope_phase_statuses_are_valid() -> None:
    rows = _phase_rows()
    assert rows, "docs/project/mvp-scope.md has no phase rows (| F<n> ... | status |)"
    invalid = [(phase, status) for phase, status in rows if status not in PHASE_STATUSES]
    assert not invalid, (
        f"docs/project/mvp-scope.md: status must be one of {sorted(PHASE_STATUSES)}, got {invalid}"
    )


def test_agents_current_phase_matches_scope() -> None:
    in_progress = [phase for phase, status in _phase_rows() if status == "in progress"]
    assert len(in_progress) == 1, (
        f"docs/project/mvp-scope.md must have exactly one `in progress` phase, got {in_progress}"
    )
    match = re.search(r"\*\*Current phase\*\*:\s*(F\d+(?:\.\d+)?)\b", _read("AGENTS.md"))
    assert match, "AGENTS.md needs a line `**Current phase**: F<n> ...` (Workflow expectations)"
    assert match.group(1) == in_progress[0], (
        f"AGENTS.md says current phase {match.group(1)} but docs/project/mvp-scope.md has "
        f"{in_progress[0]} in progress"
    )
