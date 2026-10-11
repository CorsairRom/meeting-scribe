"""``SecretKeyStore`` maps libsecret failures to codes (BR-005). The keyring is never touched.

Runs under system Python with ``Secret.password_store`` stubbed; skipped without gi or libsecret.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests.system_python import INTERPRETER, skip_without_toolkit

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
_SKIP = 77  # the child exits 77 (the automake "skipped" code) when libsecret cannot be imported

_SCRIPT = """
import sys
sys.path.insert(0, __APP_SRC__)
import gi
try:
    gi.require_version("Secret", "1")
except ValueError:
    sys.exit(__SKIP__)
from gi.repository import Gio, GLib
from meeting_scribe_review.core.result import Err, Ok
from meeting_scribe_review.ui import key_store

def error(code):
    return GLib.Error.new_literal(Gio.dbus_error_quark(), "fake", code)

def run(failure):
    out = []
    def fake_store(schema, attributes, collection, label, password, cancellable, callback):
        callback(None, None)
    def fake_finish(result):
        if failure is not None:
            raise failure
        return True
    if key_store.Secret is not None:
        key_store.Secret.password_store = fake_store
        key_store.Secret.password_store_finish = fake_finish
    key_store.SecretKeyStore().store("fake-key", out.append)
    return out[0]

def code(result):
    return result.error.code if isinstance(result, Err) else "ok"

print(code(run(None)))
print(code(run(error(Gio.DBusError.SERVICE_UNKNOWN))))
print(code(run(error(Gio.DBusError.NAME_HAS_NO_OWNER))))
print(code(run(error(Gio.DBusError.FAILED))))
key_store.Secret = None
print(code(run(None)))
"""


def test_libsecret_failures_map_to_keyring_codes() -> None:
    skip_without_toolkit()
    script = _SCRIPT.replace("__APP_SRC__", repr(str(APP_SRC))).replace("__SKIP__", str(_SKIP))
    done = subprocess.run(  # noqa: S603
        [INTERPRETER, "-I", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},
    )
    if done.returncode == _SKIP:
        pytest.skip("libsecret typelib is not available")
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [
        "ok",
        "KEYRING_UNAVAILABLE",
        "KEYRING_UNAVAILABLE",
        "KEYRING_STORE_FAILED",
        "KEYRING_UNAVAILABLE",
    ]
