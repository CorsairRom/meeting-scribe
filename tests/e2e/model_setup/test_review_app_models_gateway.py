"""The app's ``DBusGateway`` against the real ``Models1`` on a private bus (AC-18, AC-19).

The gateway runs in a system-Python subprocess whose session bus is the private one: no window,
no real session bus, no keyring, no network, no model, no audio. The service side is fed a fake
download runner, cache probe and token source (the token is a sentinel). The sentinel must appear
nowhere the client can see: not in its output, not in its logs, and no gateway call takes one.
"""

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from meeting_scribe.main import ModelSetupAdapters
from meeting_scribe.model_setup.domain.models import CacheStatus
from tests.e2e.rig import ServiceRig, running
from tests.system_python import INTERPRETER, skip_without_toolkit
from tests.unit.model_setup.fakes import SENTINEL, FakeCacheProbe, FakeRunner, FakeTokenSource

pytestmark = pytest.mark.dbus

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
DEADLINE_SECONDS = 20
NONE = CacheStatus(False, False)
BOTH = CacheStatus(True, True)

_CLIENT = """
import inspect, json, sys
sys.path.insert(0, __APP_SRC__)
from gi.repository import GLib
from meeting_scribe_review.core.result import Err
from meeting_scribe_review.ui.dbus_gateway import DBusGateway

MODE = __MODE__
loop = GLib.MainLoop()
results, events, state = {}, [], {"cancelled": False}

def shape(result):
    if isinstance(result, Err):
        return ["err", result.error.code]
    value = result.value
    if hasattr(value, "whisper_cached"):
        return [value.state, value.whisper_cached, value.pipeline_cached, value.token_stored,
                value.step, value.total_steps, value.failure_code]
    return value

def status(name, then):
    def done(result):
        results[name] = shape(result)
        then()
    gateway.models_status(done)

def after_finished():
    if MODE != "absent":
        status("after", loop.quit)

class Listener:
    def on_service_available(self, available):
        events.append(["available", available])
    def on_models_progress(self, model, step, total):
        events.append(["progress", model, step, total])
        if MODE == "cancel" and step == 2 and not state["cancelled"]:
            state["cancelled"] = True
            gateway.cancel_download(lambda r: results.__setitem__("cancel", shape(r)))
    def on_models_finished(self, outcome, code):
        events.append(["finished", outcome, code])
        after_finished()

gateway = DBusGateway()
listener = Listener()
gateway.watch_models(listener)
results["signatures"] = {
    name: list(inspect.signature(getattr(gateway, name)).parameters)
    for name in ("models_status", "download_models", "cancel_download")
}

def downloaded(result):
    results["download"] = shape(result)

if MODE == "absent":
    status("only", loop.quit)
else:
    status("before", lambda: gateway.download_models(downloaded))

def give_up():
    results["timeout"] = True
    loop.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add_seconds(__DEADLINE__, give_up)
loop.run()
gateway.unwatch_models(listener)
print(json.dumps({"results": results, "events": events}))
"""


async def _client(address: str, mode: str) -> tuple[dict, str]:
    env = {
        "PATH": os.environ["PATH"],
        "DBUS_SESSION_BUS_ADDRESS": address,
        "HOME": str(Path("/nonexistent")),
    }
    script = (
        _CLIENT.replace("__APP_SRC__", repr(str(APP_SRC)))
        .replace("__MODE__", repr(mode))
        .replace("__DEADLINE__", str(DEADLINE_SECONDS))
    )
    proc = await asyncio.create_subprocess_exec(
        INTERPRETER,
        "-I",
        "-c",
        script,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
    assert proc.returncode == 0, err.decode()
    return json.loads(out), out.decode() + err.decode()


def _rig(tmp_path: Path, bus_address: str, runner: FakeRunner, probe: FakeCacheProbe) -> ServiceRig:
    adapters = ModelSetupAdapters(FakeTokenSource(SENTINEL), probe, runner)
    return ServiceRig(tmp_path, bus_address=bus_address, model_setup=adapters)


def _run(rig: ServiceRig, address: str, mode: str) -> tuple[dict, str]:
    async def main() -> tuple[dict, str]:
        async with running(rig, address):
            return await _client(address, mode)

    return asyncio.run(main())


def test_the_gateway_follows_a_download_to_ready(tmp_path: Path, bus_address: str) -> None:
    skip_without_toolkit()
    runner = FakeRunner()
    rig = _rig(tmp_path, bus_address, runner, FakeCacheProbe(NONE, NONE, BOTH))

    out, seen = _run(rig, bus_address, "complete")

    results = out["results"]
    assert "timeout" not in results, out
    assert results["before"] == ["PENDING", False, False, True, 0, 0, ""]
    assert results["download"] == "DOWNLOADING"
    assert ["progress", "pipeline", 1, 2] in out["events"]
    assert ["progress", "whisper", 2, 2] in out["events"]
    assert ["finished", "READY", ""] in out["events"]
    assert results["after"] == ["READY", True, True, True, 0, 0, ""]
    # the service did read the token (it is the one that downloads) and the client saw none of it
    assert runner.runs[0][0] == SENTINEL
    assert SENTINEL not in seen
    assert results["signatures"] == {
        "models_status": ["done"],
        "download_models": ["done"],
        "cancel_download": ["done"],
    }


def test_the_gateway_cancels_a_running_download(tmp_path: Path, bus_address: str) -> None:
    skip_without_toolkit()

    gate = threading.Event()  # never set: only a cancel ends the fake download
    rig = _rig(tmp_path, bus_address, FakeRunner(gate=gate), FakeCacheProbe(NONE))

    out, seen = _run(rig, bus_address, "cancel")

    results = out["results"]
    assert "timeout" not in results, out
    assert results["cancel"] is True
    assert ["finished", "CANCELLED", ""] in out["events"]
    assert results["after"] == ["PENDING", False, False, True, 0, 0, ""]
    assert SENTINEL not in seen


def test_without_the_service_the_gateway_says_it_is_not_running(bus_address: str) -> None:
    skip_without_toolkit()

    async def main() -> tuple[dict, str]:
        return await _client(bus_address, "absent")

    out, _ = asyncio.run(main())

    assert out["results"]["only"] == ["err", "SERVICE_NOT_RUNNING"]
