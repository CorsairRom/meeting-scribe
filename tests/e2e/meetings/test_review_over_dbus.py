"""The speaker review members of ``Meetings1`` on a private bus (AC-2, AC-4, AC-8, AC-9, AC-10).

Synthetic meeting folders under ``tmp_path`` only; names are obviously fake.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from dbus_fast import DBusError
from dbus_fast.aio import MessageBus

from meeting_scribe.meetings.domain.meeting import SpeakerReview
from tests.e2e.rig import Proxy, ServiceRig, connect, eventually, interface, running

pytestmark = pytest.mark.dbus

IFACE = "dev.meetingscribe.Meetings1"
ERROR = "dev.meetingscribe.Error"
LINES = [
    ("S1", 0.0, 2.0, "Fake line one."),
    ("S2", 2.0, 4.5, "Fake line two."),
    ("S3", 4.5, 6.0, "Fake line three."),
]
WAV = b"RIFF\x00\x00\x00\x00WAVEfake"


class Session(NamedTuple):
    client: MessageBus
    meetings: Proxy


def _run(rig: ServiceRig, address: str, scenario: Callable[[Session], Awaitable[None]]) -> None:
    async def main() -> None:
        async with running(rig, address):
            client = await connect(address)
            try:
                await scenario(Session(client, await interface(client, IFACE)))
            finally:
                client.disconnect()

    asyncio.run(main())


def _rows(raw: list[dict]) -> list[dict]:
    return [{key: variant.value for key, variant in row.items()} for row in raw]


async def _error(call: Awaitable) -> str:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value.type


def test_get_speakers_and_transcript(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": WAV})
    rig.seed_reviewable("quiet", 10, [])

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        rows = _rows(await meetings.call_get_speakers("m1"))
        assert [(r["id"], r["display"], r["has_sample"], r["merged_into"]) for r in rows] == [
            ("S1", "Speaker 1", True, ""),
            ("S2", "Speaker 2", False, ""),
            ("S3", "Speaker 3", False, ""),
        ]
        assert await meetings.call_get_transcript("m1") == LINES
        assert await meetings.call_get_transcript("quiet") == []

    _run(rig, bus_address, scenario)


def test_get_speaker_sample_returns_bytes_and_typed_errors(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": WAV})
    rig.seed_failed("bad", 30)

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        assert bytes(await meetings.call_get_speaker_sample("m1", "S1")) == WAV
        assert await _error(meetings.call_get_speaker_sample("m1", "S2")) == (
            f"{ERROR}.ENTITY_NOT_FOUND_SPEAKER_SAMPLE"
        )
        assert await _error(meetings.call_get_speaker_sample("m1", "../S1")) == (
            f"{ERROR}.ENTITY_NOT_FOUND_SPEAKER"
        )
        assert await _error(meetings.call_get_speaker_sample("nope", "S1")) == (
            f"{ERROR}.ENTITY_NOT_FOUND_MEETING"
        )
        assert await _error(meetings.call_get_speaker_sample("bad", "S1")) == (
            f"{ERROR}.MEETING_NOT_PROCESSED"
        )

    _run(rig, bus_address, scenario)


def test_a_maximum_size_sample_fits_in_one_reply(tmp_path: Path, bus_address: str) -> None:
    sample = bytes(range(256)) * ((16_000 * 2 * 15 + 44) // 256 + 1)  # A-4: 15 s, 16 kHz, 16-bit
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": sample})

    async def scenario(session: Session) -> None:
        assert bytes(await session.meetings.call_get_speaker_sample("m1", "S1")) == sample

    _run(rig, bus_address, scenario)


def test_rename_merge_unmerge_persist_and_announce(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, {"S1": WAV})
    transcript = tmp_path / "meetings" / "m1" / "transcript.md"

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        announced: list[str] = []
        meetings.on_speakers_changed(announced.append)

        renamed = _rows(await meetings.call_rename_speaker("m1", "S1", "  Fake Name  "))
        assert renamed[0]["name"] == "Fake Name" and renamed[0]["display"] == "Fake Name"
        await eventually(lambda: announced == ["m1"], "rename announced")
        assert rig.repository.meetings["m1"].speakers[0].name == "Fake Name"
        assert "Fake Name" in transcript.read_text(encoding="utf-8")

        merged = _rows(await meetings.call_merge_speakers("m1", "S3", "S1"))
        assert merged[-1]["id"] == "S3" and merged[-1]["merged_into"] == "S1"
        shown = await meetings.call_get_transcript("m1")
        assert [line[0] for line in shown] == ["S1", "S2", "S1"]
        assert (await meetings.call_list())[0][4] == 2  # active speakers
        assert (await meetings.call_get("m1"))["speaker_count"].value == 2
        await eventually(lambda: announced == ["m1", "m1"], "merge announced")

        restored = _rows(await meetings.call_unmerge_speaker("m1", "S3"))
        assert all(row["merged_into"] == "" for row in restored)
        await eventually(lambda: announced == ["m1", "m1", "m1"], "unmerge announced")

        await meetings.call_unmerge_speaker("m1", "S3")  # a no-op: succeeds, no signal
        await asyncio.sleep(0.2)
        assert announced == ["m1", "m1", "m1"]

    _run(rig, bus_address, scenario)


def test_edit_errors_are_named_and_never_quote_the_name(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    rig.seed_failed("bad", 30)

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        await meetings.call_rename_speaker("m1", "S1", "Fake Name")
        with pytest.raises(DBusError) as taken:
            await meetings.call_rename_speaker("m1", "S2", "fake name")
        assert taken.value.type == f"{ERROR}.SPEAKER_NAME_TAKEN"
        assert "fake name" not in taken.value.text.lower()
        assert await _error(meetings.call_rename_speaker("m1", "S2", "x" * 65)) == (
            f"{ERROR}.INVALID_VALUE"
        )
        assert await _error(meetings.call_merge_speakers("m1", "S1", "S1")) == (
            f"{ERROR}.SPEAKER_MERGE_INVALID"
        )
        assert await _error(meetings.call_unmerge_speaker("m1", "S9")) == (
            f"{ERROR}.ENTITY_NOT_FOUND_SPEAKER"
        )
        assert await _error(meetings.call_rename_speaker("bad", "S1", "Fake")) == (
            f"{ERROR}.MEETING_NOT_PROCESSED"
        )
        assert await _error(meetings.call_get_transcript("nope")) == (
            f"{ERROR}.ENTITY_NOT_FOUND_MEETING"
        )

    _run(rig, bus_address, scenario)


def test_the_service_repairs_a_stale_transcript_at_start(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES, review=SpeakerReview(transcript_stale=True))
    transcript = tmp_path / "meetings" / "m1" / "transcript.md"
    assert not transcript.exists()

    async def scenario(_session: Session) -> None:
        assert transcript.is_file()
        assert not rig.repository.meetings["m1"].speaker_review.transcript_stale

    _run(rig, bus_address, scenario)


def test_a_reprocess_that_finishes_announces_new_speakers(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        announced: list[str] = []
        meetings.on_speakers_changed(announced.append)
        await meetings.call_reprocess("m1")
        await eventually(lambda: rig.state("m1") == "DONE" and announced == ["m1"], "announced")

    _run(rig, bus_address, scenario)
