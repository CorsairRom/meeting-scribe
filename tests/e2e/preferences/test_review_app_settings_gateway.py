"""The app's ``DBusGateway`` against the real ``Settings1`` on a private bus (AC-1, AC-2, AC-15).

The gateway runs in a system-Python subprocess whose session bus is the private one: no window,
no real session bus, no keyring, no network, no audio. The settings file lives under ``tmp_path``.
``Restart`` is never called: that would ask a systemd manager.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from returns.result import Success

from meeting_scribe.summaries.domain.ports import ModelEntry
from tests.e2e.rig import ServiceRig, running
from tests.system_python import INTERPRETER, skip_without_toolkit

pytestmark = pytest.mark.dbus

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
DEADLINE_SECONDS = 20

_CLIENT = """
import json, sys
sys.path.insert(0, __APP_SRC__)
from gi.repository import GLib
from meeting_scribe_review.core.result import Err, Ok
from meeting_scribe_review.ui.dbus_gateway import DBusGateway

loop = GLib.MainLoop()
results, events, state = {}, [], {"steps_done": False, "changed": False}

def maybe_quit():
    if state["steps_done"] and state["changed"]:
        loop.quit()

class Listener:
    def on_service_available(self, available):
        events.append(["available", available])
    def on_settings_changed(self, keys):
        events.append(["changed", list(keys)])
        state["changed"] = True
        maybe_quit()

class Late:
    # Registered after the availability is known: it must be told at once.
    def on_service_available(self, available):
        events.append(["late", available])
    def on_settings_changed(self, keys):
        pass

gateway = DBusGateway()
first = Listener()
gateway.watch_settings(first)
late = Late()

def shape(value):
    if isinstance(value, list):
        return [[row.key, row.value, row.source, row.locked, row.apply] for row in value]
    if hasattr(value, "models"):
        return [value.status, [[m.id, m.supported, m.reason] for m in value.models]]
    if hasattr(value, "errors"):
        return [list(value.applied), list(value.restart_required), value.apply_failed,
                [[e.key, e.code] for e in value.errors]]
    return list(value) if isinstance(value, tuple) else value

steps = [
    ("get_all", lambda d: gateway.get_all(d)),
    ("set_bad", lambda d: gateway.set_values({"summaries.language": "Not Valid"}, d)),
    ("set_good", lambda d: gateway.set_values({"summaries.language": "es"}, d)),
    ("pending", lambda d: gateway.restart_required(d)),
    ("after", lambda d: gateway.get_all(d)),
    ("models", lambda d: gateway.list_models("cloud", d)),
]

def register_late(done):
    gateway.watch_settings(late)  # the name was reported long ago: it must hear it now
    done(Ok(None))

steps.append(("late", register_late))

def run(index=0):
    if index == len(steps):
        state["steps_done"] = True
        maybe_quit()
        return
    name, call = steps[index]
    def done(result):
        failed = isinstance(result, Err)
        results[name] = ["err", result.error.code] if failed else shape(result.value)
        run(index + 1)
    call(done)

stray = Late()
gateway.watch_settings(stray)
gateway.unwatch_settings(stray)
results["unwatched"] = stray not in gateway._settings_listeners

def give_up():
    results["timeout"] = True
    loop.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add_seconds(__DEADLINE__, give_up)
run()
loop.run()
print(json.dumps({"results": results, "events": events}))
"""


async def _client(address: str) -> dict:
    env = {
        "PATH": os.environ["PATH"],
        "DBUS_SESSION_BUS_ADDRESS": address,
        "HOME": str(Path("/nonexistent")),
    }
    proc = await asyncio.create_subprocess_exec(
        INTERPRETER,
        "-I",
        "-c",
        _CLIENT.replace("__APP_SRC__", repr(str(APP_SRC))).replace(
            "__DEADLINE__", str(DEADLINE_SECONDS)
        ),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
    assert proc.returncode == 0, err.decode()
    return json.loads(out)


def test_the_gateway_speaks_the_real_settings_contract(tmp_path: Path, bus_address: str) -> None:
    skip_without_toolkit()
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.cloud_models.result = Success(
        [
            ModelEntry(id="glm-5"),
            ModelEntry(id="gpt-5", supported=False, reason="NOT_SUPPORTED_YET"),
        ]
    )

    async def main() -> dict:
        async with running(rig, bus_address):
            return await _client(bus_address)

    out = asyncio.run(main())
    results = out["results"]
    assert "timeout" not in results, out
    assert results["get_all"][0] == ["summaries.provider", "cloud", "file", False, "live"]
    assert results["get_all"][2][:3] == ["summaries.language", "auto", "default"]
    assert results["set_bad"] == [[], [], False, [["summaries.language", "FIELD_INVALID"]]]
    assert results["set_good"] == [["summaries.language"], [], False, []]
    assert results["pending"] == []
    assert results["after"][2][:3] == ["summaries.language", "es", "file"]
    assert ["changed", ["summaries.language"]] in out["events"]
    assert ["late", True] in out["events"]  # told the current availability on registering
    assert results["models"] == [
        "OK",
        [["glm-5", True, ""], ["gpt-5", False, "NOT_SUPPORTED_YET"]],
    ]
    assert results["unwatched"] is True
