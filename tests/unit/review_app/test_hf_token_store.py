"""The Hugging Face token goes to the keyring with the attributes the service reads (AC-19).

Runs under system Python with libsecret stubbed: the real keyring is never opened. The token is
proved absent from everything the store logs. Skipped without gi or libsecret.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.system_python import INTERPRETER, skip_without_toolkit

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
_SKIP = 77  # the child exits 77 (the automake "skipped" code) when libsecret cannot be imported

_SCRIPT = """
import io, json, logging, sys
sys.path.insert(0, __APP_SRC__)
import gi
try:
    gi.require_version("Secret", "1")
except ValueError:
    sys.exit(__SKIP__)
from gi.repository import Gio, GLib
from meeting_scribe_review.core.constants import HF_ATTRIBUTES, HF_LABEL
from meeting_scribe_review.core.result import Err
from meeting_scribe_review.ui import key_store

SENTINEL = "hf_SENTINELtoken0123456789"
log = io.StringIO()
handler = logging.StreamHandler(log)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.DEBUG)

def error(code):
    return GLib.Error.new_literal(Gio.dbus_error_quark(), "fake " + SENTINEL, code)

calls = {}
failure = [None]

def fake_store(schema, attributes, collection, label, password, cancellable, callback):
    calls["attributes"] = dict(attributes)
    calls["schema_name"] = schema.name
    calls["label"] = label
    calls["password_is_token"] = password == SENTINEL
    callback(None, None)

def fake_finish(result):
    if failure[0] is not None:
        raise failure[0]
    return True

key_store.Secret.password_store = fake_store
key_store.Secret.password_store_finish = fake_finish

def store(fail=None):
    failure[0] = fail
    out = []
    key_store.hugging_face_token_store().store(SENTINEL, out.append)
    return out[0]

ok = store()
codes = [store(error(Gio.DBusError.SERVICE_UNKNOWN)).error.code,
         store(error(Gio.DBusError.FAILED)).error.code]
print(json.dumps({
    "ok": not isinstance(ok, Err),
    "calls": calls,
    "constants": [HF_ATTRIBUTES, HF_LABEL],
    "codes": codes,
    "log_has_token": SENTINEL in log.getvalue(),
    "log": log.getvalue(),
}))
"""


def _run() -> dict:
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
    return json.loads(done.stdout)


def test_the_token_is_stored_under_the_item_the_service_reads() -> None:
    out = _run()
    assert out["ok"] is True
    assert out["calls"]["attributes"] == {
        "application": "meeting-scribe",
        "key": "huggingface-token",
    }
    assert out["calls"]["schema_name"] == "org.freedesktop.Secret.Generic"  # what secret-tool uses
    assert out["calls"]["label"] == "Meeting Scribe Hugging Face token"
    assert out["calls"]["password_is_token"] is True
    assert out["constants"] == [
        {"application": "meeting-scribe", "key": "huggingface-token"},
        "Meeting Scribe Hugging Face token",
    ]


def test_a_failed_store_logs_a_code_and_never_the_token() -> None:
    out = _run()
    assert out["codes"] == ["KEYRING_UNAVAILABLE", "KEYRING_STORE_FAILED"]
    assert out["log_has_token"] is False
    assert "Hugging Face token failed: KEYRING_STORE_FAILED" in out["log"]
