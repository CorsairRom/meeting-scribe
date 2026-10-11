"""``Summaries1`` end to end on a private bus (AC-1, AC-2, AC-4, AC-5, AC-12, AC-14, AC-19).

Fake providers and a fake key store, synthetic meetings under ``tmp_path``: no model, no network,
no keyring, no audio. The summary files are real files in the temp meeting folders.
"""

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from dbus_fast import DBusError
from dbus_fast.aio import MessageBus

from meeting_scribe.summaries.domain.errors import SummaryCloudKeyMissingError
from tests.e2e.rig import Proxy, ServiceRig, connect, eventually, interface, running
from tests.unit.meetings.fakes import FakeKeyStore

pytestmark = pytest.mark.dbus

IFACE = "dev.meetingscribe.Summaries1"
ERROR = "dev.meetingscribe.Error"
LINES = [
    ("S1", 0.0, 2.0, "Fake line one."),
    ("S2", 2.0, 4.5, "Fake line two."),
    ("S3", 4.5, 6.0, "Fake line three."),
]


class Session(NamedTuple):
    client: MessageBus
    summaries: Proxy
    meetings: Proxy
    recording: Proxy
    props: Proxy


def _run(rig: ServiceRig, address: str, scenario: Callable[[Session], Awaitable[None]]) -> None:
    async def main() -> None:
        async with running(rig, address):
            client = await connect(address)
            try:
                props = await interface(client, "org.freedesktop.DBus.Properties")
                session = Session(
                    client,
                    await interface(client, IFACE),
                    await interface(client, "dev.meetingscribe.Meetings1"),
                    await interface(client, "dev.meetingscribe.Recording1"),
                    props,
                )
                await scenario(session)
            finally:
                client.disconnect()

    asyncio.run(main())


def _plain(raw: dict) -> dict:
    return {key: variant.value for key, variant in raw.items()}


async def _error(call: Awaitable) -> DBusError:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value


async def _summarize_once_the_gpu_is_free(summaries: Proxy, meeting_id: str) -> str:
    """Start a local summary, retrying only while processing still holds the GPU."""
    busy = {f"{ERROR}.SUMMARY_GPU_BUSY", f"{ERROR}.SUMMARY_BUSY"}
    deadline = time.monotonic() + 10
    while True:
        try:
            return await summaries.call_summarize(meeting_id, "local", False)
        except DBusError as error:
            if error.type not in busy or time.monotonic() > deadline:
                raise
        await asyncio.sleep(0.05)


def test_the_providers_come_from_the_service_with_the_cloud_label(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)

    async def scenario(session: Session) -> None:
        rows = [_plain(row) for row in await session.summaries.call_providers()]
        assert [(r["id"], r["cloud"], r["available"], r["default"]) for r in rows] == [
            ("local", False, True, True),
            ("cloud", True, True, False),
        ]
        assert "cloud" in rows[1]["label"]
        assert rows[0]["label"].startswith("Local (")
        assert all(
            set(r) == {"id", "label", "cloud", "model", "available", "reason", "default"}
            for r in rows
        )
        assert rig.keys.reads == 0  # listing never reads the key

    _run(rig, bus_address, scenario)


def test_a_local_summary_runs_announces_and_is_readable(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    progress: list[tuple[str, int, int]] = []
    finished: list[tuple[str, str, str]] = []
    active: list[str] = []
    started, release = threading.Event(), threading.Event()

    def hold(_n: int) -> None:
        started.set()
        release.wait(5)

    rig.local_provider.on_call = hold

    async def scenario(session: Session) -> None:
        summaries = session.summaries
        summaries.on_summary_progress(lambda m, s, t: progress.append((m, s, t)))
        summaries.on_summary_finished(lambda m, s, c: finished.append((m, s, c)))
        session.props.on_properties_changed(
            lambda iface, values, _: (
                active.append(values["ActiveSummaryMeetingId"].value)
                if iface == IFACE and "ActiveSummaryMeetingId" in values
                else None
            )
        )
        assert await summaries.call_summarize("m1", "local", False) == "RUNNING"
        await eventually(started.is_set, "the model call started")
        assert await summaries.get_active_summary_meeting_id() == "m1"
        status = _plain(await summaries.call_get_summary_status("m1"))
        assert (status["state"], status["cloud"], status["failure_code"]) == ("RUNNING", False, "")
        release.set()
        await eventually(lambda: finished == [("m1", "DONE", "")], "finished")
        assert progress and progress[-1][1] == progress[-1][2]  # the last step is always sent
        assert active == ["m1", ""]
        assert await summaries.get_active_summary_meeting_id() == ""

        status = _plain(await summaries.call_get_summary_status("m1"))
        assert (status["state"], status["stale"], status["step"]) == ("DONE", False, 0)
        assert status["generated_at"] != ""
        document = _plain(await summaries.call_get_summary("m1"))
        assert "Synthetic title" in document["markdown"]
        assert (document["cloud"], document["stale"], document["title"]) == (
            False,
            False,
            "Synthetic title",
        )
        assert "path" not in document
        last = _plain(await summaries.call_last_summary())
        assert last["meeting_id"] == "m1"
        assert last["path"] == str(tmp_path / "meetings" / "m1" / "summary.md")
        assert (tmp_path / "meetings" / "m1" / "summary.md").exists()

    _run(rig, bus_address, scenario)


def test_a_rename_makes_the_summary_stale(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    finished: list[str] = []

    async def scenario(session: Session) -> None:
        session.summaries.on_summary_finished(lambda *args: finished.append(args[1]))
        await session.summaries.call_summarize("m1", "local", False)
        await eventually(lambda: finished == ["DONE"], "finished")
        await session.meetings.call_merge_speakers("m1", "S3", "S1")  # the effective text changes
        status = _plain(await session.summaries.call_get_summary_status("m1"))
        assert (status["state"], status["stale"]) == ("DONE", True)

    _run(rig, bus_address, scenario)


def test_refusals_are_named_errors_that_never_leak_content_or_the_key(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    rig.seed_reviewable("m2", 10, LINES)
    rig.seed_failed("bad", 20)
    rig.keys.failure = SummaryCloudKeyMissingError("No key stored; see the runbook.")
    started, release = threading.Event(), threading.Event()

    def hold(_n: int) -> None:
        started.set()
        release.wait(5)

    rig.local_provider.on_call = hold

    async def scenario(session: Session) -> None:
        summaries = session.summaries
        errors = {
            "cloud_unconfirmed": await _error(summaries.call_summarize("m1", "cloud", False)),
            "cloud_key": await _error(summaries.call_summarize("m1", "cloud", True)),
            "unknown_provider": await _error(summaries.call_summarize("m1", "x", False)),
            "unknown_meeting": await _error(summaries.call_summarize("nope", "local", False)),
            "not_processed": await _error(summaries.call_summarize("bad", "local", False)),
            "no_summary": await _error(summaries.call_get_summary("m1")),
            "no_last": await _error(summaries.call_last_summary()),
            "cancel_unknown": await _error(summaries.call_cancel_summary("nope")),
        }
        assert {k: e.type.removeprefix(ERROR + ".") for k, e in errors.items()} == {
            "cloud_unconfirmed": "SUMMARY_CLOUD_NOT_CONFIRMED",
            "cloud_key": "SUMMARY_CLOUD_KEY_MISSING",
            "unknown_provider": "INVALID_VALUE",
            "unknown_meeting": "ENTITY_NOT_FOUND_MEETING",
            "not_processed": "MEETING_NOT_PROCESSED",
            "no_summary": "ENTITY_NOT_FOUND_SUMMARY",
            "no_last": "ENTITY_NOT_FOUND_SUMMARY",
            "cancel_unknown": "ENTITY_NOT_FOUND_MEETING",
        }
        assert all(
            "Fake line" not in e.text and FakeKeyStore.FAKE_KEY not in e.text
            for e in errors.values()
        )
        assert await summaries.call_summarize("m1", "local", False) == "RUNNING"
        await eventually(started.is_set, "running")
        again = await _error(summaries.call_summarize("m1", "local", False))
        other = await _error(summaries.call_summarize("m2", "local", False))
        assert again.type == f"{ERROR}.SUMMARY_ALREADY_RUNNING"
        assert other.type == f"{ERROR}.SUMMARY_BUSY"
        release.set()

    _run(rig, bus_address, scenario)


def test_cancel_stops_the_summary_and_is_false_when_idle(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    rig.local_provider.block_until_cancelled = True
    finished: list[tuple[str, str, str]] = []

    async def scenario(session: Session) -> None:
        summaries = session.summaries
        summaries.on_summary_finished(lambda m, s, c: finished.append((m, s, c)))
        assert await summaries.call_cancel_summary("m1") is False
        await summaries.call_summarize("m1", "local", False)
        await eventually(lambda: bool(rig.local_provider.requests), "model call started")
        assert await summaries.call_cancel_summary("m1") is True
        await eventually(
            lambda: finished == [("m1", "CANCELLED", "SUMMARY_CANCELLED")], "cancelled"
        )
        assert await summaries.get_active_summary_meeting_id() == ""
        assert (await summaries.call_get_summary_status("m1"))["state"].value == "NONE"
        assert await _error(summaries.call_get_summary("m1")) is not None  # nothing was written

    _run(rig, bus_address, scenario)


def test_a_recording_cancels_a_local_summary_and_holds_off_a_new_one(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    rig.local_provider.block_until_cancelled = True
    finished: list[tuple[str, str, str]] = []

    async def scenario(session: Session) -> None:
        summaries = session.summaries
        summaries.on_summary_finished(lambda m, s, c: finished.append((m, s, c)))
        await summaries.call_summarize("m1", "local", False)
        await eventually(lambda: bool(rig.local_provider.requests), "model call started")
        await session.recording.call_start()  # a recording is never delayed (ADR-003)
        await eventually(
            lambda: finished == [("m1", "CANCELLED", "SUMMARY_CANCELLED")], "cancelled"
        )
        busy = await _error(summaries.call_summarize("m1", "local", False))
        assert busy.type == f"{ERROR}.SUMMARY_GPU_BUSY"
        await session.recording.call_stop()
        # Stop queues the new recording for processing, and processing may take the GPU before
        # this call does; a local summary is then refused until processing is done.
        assert await _summarize_once_the_gpu_is_free(summaries, "m1") == "RUNNING"
        await summaries.call_cancel_summary("m1")

    _run(rig, bus_address, scenario)


def test_a_confirmed_cloud_summary_is_labelled_cloud_and_ignores_the_gpu(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    finished: list[tuple[str, str, str]] = []

    async def scenario(session: Session) -> None:
        summaries = session.summaries
        summaries.on_summary_finished(lambda m, s, c: finished.append((m, s, c)))
        await session.recording.call_start()
        assert await summaries.call_summarize("m1", "cloud", True) == "RUNNING"
        await eventually(lambda: finished == [("m1", "DONE", "")], "finished")
        status = _plain(await summaries.call_get_summary_status("m1"))
        assert status["cloud"] is True and "cloud" in status["provider_label"]
        assert _plain(await summaries.call_get_summary("m1"))["cloud"] is True
        assert rig.keys.reads >= 1  # read only for the confirmed request
        await session.recording.call_stop()

    _run(rig, bus_address, scenario)


def _serve_and_stop(rig: ServiceRig, address: str, timeout: float) -> int:
    from meeting_scribe.main import serve  # noqa: PLC0415

    async def main() -> int:
        ready, stop = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(
            serve(rig.app, bus_address=address, ready=ready, stop=stop, shutdown_timeout=timeout)
        )
        await asyncio.wait_for(ready.wait(), timeout=5)
        client = await connect(address)
        try:
            summaries = await interface(client, IFACE)
            await summaries.call_summarize("m1", "local", False)
            await eventually(lambda: bool(rig.local_provider.requests), "model call started")
        finally:
            client.disconnect()
        stop.set()
        return await asyncio.wait_for(task, timeout=5)

    return asyncio.run(main())


def test_serve_cancels_and_joins_a_running_summary_before_processing_stops(
    tmp_path: Path, bus_address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    rig.local_provider.block_until_cancelled = True
    order: list[str] = []
    real_shutdown = rig.app.processing.shutdown
    monkeypatch.setattr(
        rig.app.processing,
        "shutdown",
        lambda: (
            order.append(
                f"processing stops, summary idle={rig.app.summaries.active_meeting_id == ''}"
            ),
            real_shutdown(),
        )[1],
    )

    assert _serve_and_stop(rig, bus_address, 5.0) == 0

    assert order == ["processing stops, summary idle=True"]
    assert rig.repository.meetings["m1"].summary is None  # cancelled: nothing saved


def test_a_summary_that_outlives_the_shutdown_bound_exits_with_code_four_in_time(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_reviewable("m1", 0, LINES)
    release = threading.Event()
    rig.local_provider.on_call = lambda _n: (release.wait(5), None)[1]  # ignores the cancel
    started = time.monotonic()

    try:
        code = _serve_and_stop(rig, bus_address, 0.2)
    finally:
        release.set()

    assert code == 4
    assert time.monotonic() - started < 4.0
