"""Every ``ui`` module imports under system Python with GTK 4 and libadwaita (spec section 11).

Nothing is constructed and the subprocess has no display variable, so no window can open.
Skipped without ``gi``.
"""

import ast
import os
import subprocess
from pathlib import Path

import pytest

from tests.system_python import INTERPRETER, skip_without_toolkit

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
UI = APP_SRC / "meeting_scribe_review" / "ui"


def _modules() -> list[str]:
    return sorted(
        ".".join(path.relative_to(APP_SRC).with_suffix("").parts).removesuffix(".__init__")
        for path in UI.glob("*.py")
    )


def test_every_ui_module_imports_without_opening_a_display() -> None:
    skip_without_toolkit()
    script = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(APP_SRC)!r})\n"
        f"for name in {_modules()!r}:\n"
        "    importlib.import_module(name)\n"
        "importlib.import_module('meeting_scribe_review.__main__')\n"
        "from gi.repository import Gdk\n"
        "assert Gdk.Display.get_default() is None, 'a display was opened'\n"
    )
    # No display variable: importing Gtk tries to connect, which must find nothing to connect to.
    done = subprocess.run(  # noqa: S603
        [INTERPRETER, "-I", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},
    )
    assert done.returncode == 0, done.stderr


def test_ui_modules_never_open_a_path_or_import_the_service() -> None:
    for path in UI.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names] + [getattr(node, "module", "") or ""]
                assert not any(
                    name.split(".")[0] in {"pathlib", "os", "shutil", "meeting_scribe"}
                    for name in names
                ), f"{path.name} imports a file or service module"
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                pytest.fail(f"{path.name} calls open()")
