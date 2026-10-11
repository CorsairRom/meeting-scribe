"""The system interpreter the review app runs on, and whether it can host its toolkit."""

import shutil
import subprocess

import pytest

INTERPRETER = "/usr/bin/python3"
_PROBE = (
    "import gi\n"
    "gi.require_version('Gtk', '4.0')\n"
    "gi.require_version('Adw', '1')\n"
    "from gi.repository import Adw, Gtk\n"
)


def toolkit_available() -> bool:
    """True when system Python can import GTK 4 and libadwaita 1 (no display is needed)."""
    if shutil.which(INTERPRETER) is None:
        return False
    probe = subprocess.run(  # noqa: S603
        [INTERPRETER, "-I", "-c", _PROBE], capture_output=True, check=False, env={}
    )
    return probe.returncode == 0


def skip_without_toolkit() -> None:
    if not toolkit_available():
        pytest.skip("system Python with GTK 4 and libadwaita is not available")
