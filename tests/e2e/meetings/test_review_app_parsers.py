"""The app parsers accept the real replies of ``MeetingsInterface`` (private bus, spec section 11).

Synthetic meeting folders only; names are obviously fake.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from meeting_scribe_review.core.models import Utterance
from meeting_scribe_review.core.parsers import (
    parse_meeting_summaries,
    parse_speakers,
    parse_transcript,
)
from meeting_scribe_review.core.result import Ok
from tests.e2e.rig import ServiceRig, connect, interface, running

pytestmark = pytest.mark.dbus

LINES = [("S1", 0.0, 2.0, "Fake line one."), ("S2", 2.0, 4.5, "Fake line two.")]


def _plain(value: Any) -> Any:
    """What ``Gio.DBusProxy`` hands the app: variants unwrapped, containers as lists and dicts."""
    if hasattr(value, "signature") and hasattr(value, "value"):
        return _plain(value.value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def test_parsers_accept_the_real_replies(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": b"RIFF"})

    async def scenario() -> None:
        async with running(rig, bus_address):
            client = await connect(bus_address)
            try:
                meetings = await interface(client, "dev.meetingscribe.Meetings1")
                listed = parse_meeting_summaries(_plain(await meetings.call_list()))
                assert isinstance(listed, Ok) and [m.id for m in listed.value] == ["m1"]
                assert listed.value[0].state == "DONE"
                renamed = parse_speakers(
                    _plain(await meetings.call_rename_speaker("m1", "S1", "Fake Name"))
                )
                assert isinstance(renamed, Ok)
                assert [(s.id, s.display, s.has_sample) for s in renamed.value[:2]] == [
                    ("S1", "Fake Name", True),
                    ("S2", "Speaker 2", False),
                ]
                shown = parse_transcript(_plain(await meetings.call_get_transcript("m1")))
                assert shown == Ok([Utterance(*line) for line in LINES])
            finally:
                client.disconnect()

    asyncio.run(scenario())
