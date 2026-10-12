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


def test_the_preferences_entry_points_are_wired() -> None:
    """Static check: no window can open here. The manual check opens the real ones."""
    application = (UI / "application.py").read_text(encoding="utf-8")
    window = (UI / "window.py").read_text(encoding="utf-8")
    assert "PREFERENCES_OPTION" in application
    assert "wants_preferences(options)" in application
    assert "open_preferences" in application
    assert '"win.preferences"' in window  # the primary-menu entry
    assert 'Gio.SimpleAction.new("preferences"' in window


def test_the_key_never_reaches_the_settings_widgets() -> None:
    for name in ("preferences_dialog.py", "preferences_controller.py"):
        source = (UI / name).read_text(encoding="utf-8")
        assert "PasswordEntry" not in source, name  # the secret entries have their own modules


def test_only_the_two_secret_modules_hold_a_password_entry() -> None:
    holders = {
        path.name for path in UI.glob("*.py") if "PasswordEntry" in path.read_text(encoding="utf-8")
    }
    assert holders == {"api_key_dialog.py", "models_group.py"}


def test_the_token_field_is_emptied_when_submitted_and_read_nowhere_else() -> None:
    source = (UI / "models_group.py").read_text(encoding="utf-8")
    assert source.count(".get_text()") == 1  # the apply handler: the only read of the field
    assert source.count('.set_text("")') == 1  # and it empties the field at once, never echoed
    assert "set_text(" not in source.replace('.set_text("")', "")  # nothing writes a value into it
    assert "show_peek_icon" not in source  # no peek: the value is never shown


def test_the_speech_models_group_is_wired_into_preferences() -> None:
    """Static check: no window can open here. The manual check opens the real one."""
    dialog = (UI / "preferences_dialog.py").read_text(encoding="utf-8")
    controller = (UI / "preferences_controller.py").read_text(encoding="utf-8")
    window = (UI / "window.py").read_text(encoding="utf-8")
    application = (UI / "application.py").read_text(encoding="utf-8")
    assert "ModelsGroup(" in dialog
    assert "ModelsPresenter(" in controller
    assert "self._models.open()" in controller
    assert "self._models.close()" in controller  # a closed dialog drops what is only local
    assert "self._models.dispose()" in controller
    assert "TERMS_URL" in controller  # the terms link opens the page the presenter names
    assert "hugging_face_token_store()" in application
    assert "tokens" in window


def test_the_slow_models_calls_wait_longer_than_the_service_check_and_retry_is_wired() -> None:
    gateway = (UI / "dbus_gateway.py").read_text(encoding="utf-8")
    assert gateway.count("timeout_ms=MODELS_CALL_TIMEOUT_MS") == 2  # GetStatus and Download
    assert "retry=self._models.retry" in (UI / "preferences_controller.py").read_text(
        encoding="utf-8"
    )
    assert "RETRY_LABEL" in (UI / "models_group.py").read_text(encoding="utf-8")
