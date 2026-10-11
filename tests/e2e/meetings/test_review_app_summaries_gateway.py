"""The app's ``DBusGateway`` against the real ``Summaries1`` on a private bus (AC-17).

The gateway runs in a system-Python subprocess whose session bus is the private one: no window,
no real session bus, no network, no keyring, no audio. A fake provider answers; the meeting is
synthetic.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.e2e.rig import ServiceRig, running
from tests.system_python import INTERPRETER, skip_without_toolkit

pytestmark = pytest.mark.dbus

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
DEADLINE_SECONDS = 20
LINES = [("S1", 0.0, 2.0, "Fake line one."), ("S2", 2.0, 4.5, "Fake line two.")]

_CLIENT = """
import json, sys
sys.path.insert(0, __APP_SRC__)
from gi.repository import GLib
from meeting_scribe_review.core.result import Err
from meeting_scribe_review.ui.dbus_gateway import DBusGateway

loop = GLib.MainLoop()
results, events, state = {}, [], {"steps_done": False, "finished": False}

def maybe_quit():
    if state["steps_done"] and state["finished"]:
        loop.quit()

class Listener:
    def on_summary_progress(self, meeting_id, step, total):
        events.append(["progress", meeting_id, step <= total])
    def on_summary_finished(self, meeting_id, outcome, code):
        events.append(["finished", meeting_id, outcome, code])
        state["finished"] = True
        maybe_quit()

gateway = DBusGateway()
gateway.watch_summaries(Listener())

def shape(value):
    if isinstance(value, list):
        return [
            [row.id, row.cloud, row.available, row.default, "cloud" in row.label] for row in value
        ]
    if hasattr(value, "markdown"):
        return [value.title, value.cloud, value.stale, "Synthetic title" in value.markdown]
    if hasattr(value, "state"):
        return [value.state, value.stale, value.has_summary, value.failure_code]
    return value

steps = [
    ("providers", lambda d: gateway.providers(d)),
    ("status_none", lambda d: gateway.get_summary_status("m1", d)),
    ("not_confirmed", lambda d: gateway.summarize("m1", "cloud", d, cloud_confirmed=False)),
    ("cancel_idle", lambda d: gateway.cancel_summary("m1", d)),
    ("no_summary", lambda d: gateway.get_summary("m1", d)),
    ("unknown_meeting", lambda d: gateway.get_summary_status("nope", d)),
    ("summarize", lambda d: gateway.summarize("m1", "local", d, cloud_confirmed=False)),
]

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


def test_the_gateway_speaks_the_real_summaries_contract(tmp_path: Path, bus_address: str) -> None:
    skip_without_toolkit()
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)

    async def main() -> dict:
        async with running(rig, bus_address):
            return await _client(bus_address)

    out = asyncio.run(main())
    results = out["results"]
    assert "timeout" not in results, out
    assert results["providers"] == [
        ["local", False, True, True, False],
        ["cloud", True, True, False, True],
    ]
    assert results["status_none"] == ["NONE", False, False, ""]
    assert results["not_confirmed"] == ["err", "SUMMARY_CLOUD_NOT_CONFIRMED"]
    assert results["cancel_idle"] is False
    assert results["no_summary"] == ["err", "ENTITY_NOT_FOUND_SUMMARY"]
    assert results["unknown_meeting"] == ["err", "ENTITY_NOT_FOUND_MEETING"]
    assert results["summarize"] == "RUNNING"
    assert ["finished", "m1", "DONE", ""] in out["events"]
    assert any(event[0] == "progress" and event[2] for event in out["events"])
