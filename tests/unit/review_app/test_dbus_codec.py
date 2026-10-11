"""The gateway's variant packing and unpacking, run on system Python where `gi` lives (AC-12).

The replies are built with `GLib.Variant` in the exact signatures of api-contracts.md, then decoded
by the codec and parsed by the core parsers. No bus, no window, no audio device is involved.
"""

import json
import os
import subprocess
from pathlib import Path

from tests.system_python import INTERPRETER, skip_without_toolkit

APP_SRC = Path(__file__).resolve().parents[3] / "review-app" / "src"

_PRELUDE = f"""
import json, sys
sys.path.insert(0, {str(APP_SRC)!r})
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from meeting_scribe_review.ui import dbus_codec as codec
from meeting_scribe_review.core import parsers
"""


def _run(body: str) -> dict:
    skip_without_toolkit()
    done = subprocess.run(  # noqa: S603
        [INTERPRETER, "-I", "-c", _PRELUDE + body],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},  # no display variable: nothing can open a window
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_arguments_are_packed_as_one_tuple_and_no_arguments_as_none() -> None:
    out = _run(
        """
packed = codec.pack_args("sss", ("m1", "S1", "A name"))
print(json.dumps({
    "type": packed.get_type_string(),
    "value": list(packed.unpack()),
    "none": codec.pack_args("", ()) is None,
}))
"""
    )
    assert out == {"type": "(sss)", "value": ["m1", "S1", "A name"], "none": True}


def test_a_speakers_reply_unpacks_and_parses() -> None:
    out = _run(
        """
def row(i, merged):
    return {
        "id": GLib.Variant("s", i), "label": GLib.Variant("s", "Speaker 1"),
        "name": GLib.Variant("s", ""), "display": GLib.Variant("s", "Speaker 1"),
        "is_me": GLib.Variant("b", False), "talk_seconds": GLib.Variant("d", 12.5),
        "utterance_count": GLib.Variant("i", 3), "has_sample": GLib.Variant("b", True),
        "merged_into": GLib.Variant("s", merged),
    }
reply = GLib.Variant("(aa{sv})", ([row("S1", ""), row("S2", "S1")],))
result = parsers.parse_speakers(codec.unpack_reply(reply))
print(json.dumps([[s.id, s.talk_seconds, s.utterance_count, s.has_sample, s.merged_into]
                  for s in result.value]))
"""
    )
    assert out == [["S1", 12.5, 3, True, ""], ["S2", 12.5, 3, True, "S1"]]


def test_a_meeting_list_and_a_transcript_unpack_and_parse() -> None:
    out = _run(
        """
meetings = GLib.Variant("(a(ssdsi))", ([("m1", "2026-10-09T10:15:00+00:00", 754.0, "DONE", 2)],))
lines = GLib.Variant("(a(sdds))", ([("S1", 0.0, 1.5, "hello"), ("S2", 1.5, 2.0, "hi")],))
m = parsers.parse_meeting_summaries(codec.unpack_reply(meetings)).value
t = parsers.parse_transcript(codec.unpack_reply(lines)).value
print(json.dumps({"meeting": [m[0].id, m[0].state, m[0].active_speaker_count],
                  "lines": [[u.speaker_id, u.text] for u in t]}))
"""
    )
    assert out == {"meeting": ["m1", "DONE", 2], "lines": [["S1", "hello"], ["S2", "hi"]]}


def test_a_sample_reply_unpacks_to_bytes() -> None:
    out = _run(
        """
data = codec.unpack_reply(GLib.Variant("(ay)", (b"RIFF\\x00\\x01",)))
print(json.dumps({"type": type(data).__name__, "hex": data.hex()}))
"""
    )
    assert out == {"type": "bytes", "hex": "524946460001"}


def test_a_remote_error_keeps_its_service_code_and_never_its_message() -> None:
    out = _run(
        """
def code(name):
    error = Gio.DBusError.new_for_dbus_error(name, "message with a private name")
    return codec.error_from_glib(error).code
print(json.dumps({
    "service": code("dev.meetingscribe.Error.SPEAKER_NAME_TAKEN"),
    "absent": code("org.freedesktop.DBus.Error.ServiceUnknown"),
    "old": code("org.freedesktop.DBus.Error.UnknownMethod"),
    "local": codec.error_from_glib(GLib.Error.new_literal(
        Gio.io_error_quark(), "message", Gio.IOErrorEnum.TIMED_OUT)).code,
}))
"""
    )
    assert out == {
        "service": "SPEAKER_NAME_TAKEN",
        "absent": "SERVICE_NOT_RUNNING",
        "old": "SERVICE_OUTDATED",
        "local": "UNKNOWN_ERROR",
    }


def test_a_sample_becomes_a_seekable_in_memory_stream_without_touching_the_toolkit_player() -> None:
    out = _run(
        """
from meeting_scribe_review.ui import media_player
stream = media_player.sample_stream(b"RIFF\\x00\\x01tail")
print(json.dumps({
    "seekable": stream.can_seek(),
    "head": stream.read_bytes(4, None).get_data().decode(),
}))
"""
    )
    assert out == {"seekable": True, "head": "RIFF"}
