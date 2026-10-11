"""The app's ``DBusGateway`` against the real ``MeetingsInterface`` on a private bus (AC-12, AC-15).

The gateway runs in a system-Python subprocess (where ``gi`` lives) whose session bus is the
private one; nothing touches the user's session bus, a window or an audio device. Synthetic meeting
folders only; names are obviously fake.
"""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e.rig import ServiceRig, running

pytestmark = pytest.mark.dbus

INTERPRETER = "/usr/bin/python3"
APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"
LINES = [("S1", 0.0, 2.0, "Fake line one."), ("S2", 2.0, 4.5, "Fake line two.")]
WAV = b"RIFF\x00\x00\x00\x00WAVEfake"

_CLIENT = """
import json, sys
sys.path.insert(0, __APP_SRC__)
from gi.repository import GLib
from meeting_scribe_review.core.result import Err
from meeting_scribe_review.ui.dbus_gateway import DBusGateway

loop = GLib.MainLoop()
events, results = [], {}

class Listener:
    def on_service_available(self, available): events.append(["available", available])
    def on_processing_state_changed(self, meeting_id, state): events.append(["state", state])
    def on_processing_finished(self, meeting_id, state, count): events.append(["finished", state])
    def on_speakers_changed(self, meeting_id): events.append(["speakers", meeting_id])

def shape(value):
    if isinstance(value, bytes):
        return [len(value), value.startswith(b"RIFF")]
    return [
        [getattr(item, "id", None) or getattr(item, "speaker_id", None),
         getattr(item, "display", None) or getattr(item, "state", None) or item.text,
         getattr(item, "merged_into", None)]
        for item in value
    ]

gateway = DBusGateway()
gateway.watch(Listener())
steps = __STEPS__

def run(index=0):
    if index == len(steps):
        GLib.timeout_add(600, lambda: loop.quit())
        return
    name, call = steps[index]
    def done(result):
        failed = isinstance(result, Err)
        results[name] = ["err", result.error.code] if failed else shape(result.value)
        run(index + 1)
    call(done)

GLib.timeout_add_seconds(20, lambda: (results.update(timeout=True), loop.quit()))
run()
loop.run()
print(json.dumps({"results": results, "events": events}))
"""

_REVIEW_STEPS = """[
    ("list", lambda d: gateway.list_meetings(d)),
    ("speakers", lambda d: gateway.get_speakers("m1", d)),
    ("transcript", lambda d: gateway.get_transcript("m1", d)),
    ("sample", lambda d: gateway.get_speaker_sample("m1", "S1", d)),
    ("no_sample", lambda d: gateway.get_speaker_sample("m1", "S2", d)),
    ("rename", lambda d: gateway.rename_speaker("m1", "S1", "Fake Name", d)),
    ("taken", lambda d: gateway.rename_speaker("m1", "S2", "fake name", d)),
    ("merge", lambda d: gateway.merge_speakers("m1", "S2", "S1", d)),
    ("unmerge", lambda d: gateway.unmerge_speaker("m1", "S2", d)),
    ("unknown_meeting", lambda d: gateway.get_speakers("nope", d)),
]"""


def _skip_without_gi() -> None:
    if shutil.which(INTERPRETER) is None:
        pytest.skip("system Python is not available")
    probe = subprocess.run(  # noqa: S603
        [INTERPRETER, "-I", "-c", "import gi"], capture_output=True, check=False
    )
    if probe.returncode:
        pytest.skip("system Python has no gi")


async def _client(address: str, steps: str) -> dict:
    env = {
        "PATH": os.environ["PATH"],
        "DBUS_SESSION_BUS_ADDRESS": address,
        "HOME": str(Path("/nonexistent")),
    }
    proc = await asyncio.create_subprocess_exec(
        INTERPRETER,
        "-I",
        "-c",
        _CLIENT.replace("__APP_SRC__", repr(str(APP_SRC))).replace("__STEPS__", steps),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
    except TimeoutError:
        proc.kill()
        raise
    assert proc.returncode == 0, err.decode()
    return json.loads(out)


def test_the_gateway_speaks_the_real_contract(tmp_path: Path, bus_address: str) -> None:
    _skip_without_gi()
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": WAV})

    async def scenario() -> dict:
        async with running(rig, bus_address):
            return await _client(bus_address, _REVIEW_STEPS)

    out = asyncio.run(scenario())
    results = out["results"]
    assert "timeout" not in results
    assert results["list"] == [["m1", "DONE", None]]
    assert [row[0] for row in results["speakers"][:2]] == ["S1", "S2"]
    assert results["transcript"] == [["S1", "Fake line one.", None], ["S2", "Fake line two.", None]]
    assert results["sample"] == [len(WAV), True]
    assert results["no_sample"] == ["err", "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"]
    assert ["S1", "Fake Name", ""] in results["rename"]
    assert results["taken"] == ["err", "SPEAKER_NAME_TAKEN"]
    assert ["S2", "Speaker 2", "S1"] in results["merge"]
    assert ["S2", "Speaker 2", ""] in results["unmerge"]
    assert results["unknown_meeting"] == ["err", "ENTITY_NOT_FOUND_MEETING"]
    assert [event for event in out["events"] if event[0] == "speakers"] == [["speakers", "m1"]] * 3


def test_a_missing_service_is_reported_as_not_running(bus_address: str) -> None:
    _skip_without_gi()
    out = asyncio.run(_client(bus_address, '[("list", lambda d: gateway.list_meetings(d))]'))
    assert out["results"] == {"list": ["err", "SERVICE_NOT_RUNNING"]}
