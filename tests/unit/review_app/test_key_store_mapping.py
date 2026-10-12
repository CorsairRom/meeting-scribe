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


_LOOKUP_SCRIPT = """
import sys
sys.path.insert(0, __APP_SRC__)
import gi
try:
    gi.require_version("Secret", "1")
except ValueError:
    sys.exit(__SKIP__)
from gi.repository import Gio, GLib
from meeting_scribe_review.core.result import Err
from meeting_scribe_review.ui import key_store

def error(code):
    return GLib.Error.new_literal(Gio.dbus_error_quark(), "fake", code)

class Item:
    def __init__(self, locked):
        self.locked = locked
    def get_locked(self):
        return self.locked

class FakeService:
    def __init__(self, items, failure):
        self.items, self.failure, self.flags = items, failure, None
    def search(self, schema, attributes, flags, cancellable, callback):
        self.flags = flags
        callback(self, None)
    def search_finish(self, result):
        if self.failure is not None:
            raise self.failure
        return self.items

def lookup(items=(), failure=None, connect_failure=None):
    out, service = [], FakeService(list(items), failure)
    class Service:
        @staticmethod
        def get(flags, cancellable, callback):
            Service.flags = flags
            callback(None, None)
        @staticmethod
        def get_finish(result):
            if connect_failure is not None:
                raise connect_failure
            return service
    key_store.Secret.Service = Service
    key_store.SecretKeyStore().is_stored(out.append)
    return out[0], service, Service

def show(result):
    return result.error.code if isinstance(result, Err) else result.value.value

r, service, svc = lookup([])
print(show(r))
print(show(lookup([Item(False)])[0]))
print(show(lookup([Item(True)])[0]))
print(show(lookup(connect_failure=error(Gio.DBusError.SERVICE_UNKNOWN))[0]))
print(show(lookup(failure=error(Gio.DBusError.FAILED))[0]))
# no secret is ever requested: no LOAD_SECRETS and no UNLOCK in the search flags
print(int(service.flags) == 0, int(svc.flags) == 0)

def fake_clear(schema, attributes, cancellable, callback):
    callback(None, None)
def make_finish(failure):
    def finish(result):
        if failure is not None:
            raise failure
        return False
    return finish
key_store.Secret.password_clear = fake_clear
def remove(failure):
    out = []
    key_store.Secret.password_clear_finish = make_finish(failure)
    key_store.SecretKeyStore().remove(out.append)
    return "err:" + out[0].error.code if isinstance(out[0], Err) else "ok"
print(remove(None))
print(remove(error(Gio.DBusError.NAME_HAS_NO_OWNER)))
print(remove(error(Gio.DBusError.FAILED)))
key_store.Secret = None
out = []
key_store.SecretKeyStore().is_stored(out.append)
key_store.SecretKeyStore().remove(out.append)
print([show(x) for x in out])
"""


def test_the_key_lookup_and_removal_never_read_the_secret_and_map_failures() -> None:
    skip_without_toolkit()
    script = _LOOKUP_SCRIPT.replace("__APP_SRC__", repr(str(APP_SRC))).replace(
        "__SKIP__", str(_SKIP)
    )
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
    assert done.stdout.splitlines() == [
        "missing",
        "stored",
        "locked",
        "KEYRING_UNAVAILABLE",
        "KEYRING_UNAVAILABLE",
        "True True",
        "ok",
        "err:KEYRING_UNAVAILABLE",
        "err:KEYRING_REMOVE_FAILED",
        "['KEYRING_UNAVAILABLE', 'KEYRING_UNAVAILABLE']",
    ]


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
