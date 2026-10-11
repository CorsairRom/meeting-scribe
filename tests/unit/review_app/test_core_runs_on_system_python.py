"""The core is stdlib only and imports under both interpreters (AC-12, spec section 6)."""

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
CORE = APP_SRC / "meeting_scribe_review" / "core"
OWN = "meeting_scribe_review"


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_imports_only_the_standard_library_and_itself() -> None:
    allowed = set(sys.stdlib_module_names) | {OWN}
    offenders = {path.name: sorted(_imported_roots(path) - allowed) for path in CORE.rglob("*.py")}
    assert {name: roots for name, roots in offenders.items() if roots} == {}


def test_core_modules_use_absolute_imports() -> None:
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level]
        assert not relative, f"{path.name} uses a relative import"


def test_every_core_module_imports_on_system_python() -> None:
    interpreter = "/usr/bin/python3"
    if shutil.which(interpreter) is None:
        pytest.skip("system Python is not available")
    modules = sorted(
        ".".join(path.relative_to(APP_SRC).with_suffix("").parts).removesuffix(".__init__")
        for path in CORE.rglob("*.py")
    )
    script = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(APP_SRC)!r})\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
    )
    done = subprocess.run(  # noqa: S603
        [interpreter, "-I", "-c", script], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
