"""``Meetings1`` end to end on a private bus (AC-18, AC-19); runner, audio, notifier are fakes."""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from dbus_fast import DBusError
from dbus_fast.aio import MessageBus
from returns.result import Failure

from meeting_scribe.meetings.domain.errors import MeetingSchemaUnsupportedError, WorkerCrashedError
from tests.e2e.rig import Proxy, ServiceRig, connect, eventually, interface, running
from tests.unit.meetings.fakes import T0, ScriptedRunner, transcribe_ok

pytestmark = pytest.mark.dbus

IFACE = "dev.meetingscribe.Meetings1"
ERROR = "dev.meetingscribe.Error"


class Gate(ScriptedRunner):
    """Every transcribe run waits for ``release``, so a test can look at a running job."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.behaviours = {}

    def hold(self, meeting_id: str) -> None:
        def behaviour(job, on_event, cancel):
            self.entered.set()
            self.release.wait(5)
            return transcribe_ok(job, on_event, cancel)

        self.behaviours[(meeting_id, "transcribe")] = behaviour


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


def test_list_gives_every_meeting_newest_first(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_done("old", 0, speakers=2)
    rig.seed_failed("new", 30)

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        rows = await meetings.call_list()
        assert rows == [
            ("new", T0.replace(minute=30).isoformat(), 300.0, "FAILED", 0),
            ("old", T0.isoformat(), 90.0, "DONE", 2),
        ]

    _run(rig, bus_address, scenario)


def test_get_returns_the_details_and_unknown_ids_are_typed_errors(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_done("old", 0, speakers=2)
    rig.seed_failed("bad", 30)
    rig.repository.get_failures["future"] = MeetingSchemaUnsupportedError("newer schema")

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        details = {k: v.value for k, v in (await meetings.call_get("old")).items()}
        assert details == {
            "id": "old",
            "started_at": T0.isoformat(),
            "duration_seconds": 90.0,
            "directory": str(rig.meetings_dir / "old"),
            "processing_state": "DONE",
            "failure_code": "",
            "failure_reason": "",
            "speaker_count": 2,
            "transcript_path": str(rig.meetings_dir / "old" / "transcript.md"),
        }
        failed = {k: v.value for k, v in (await meetings.call_get("bad")).items()}
        assert (failed["failure_code"], failed["failure_reason"]) == (
            "MEETING_AUDIO_MISSING",
            "no chunk",
        )
        assert failed["transcript_path"] == ""
        for meeting_id, code in (
            ("nope", "ENTITY_NOT_FOUND_MEETING"),
            ("future", "MEETING_SCHEMA_UNSUPPORTED"),
        ):
            with pytest.raises(DBusError) as excinfo:
                await meetings.call_get(meeting_id)
            assert excinfo.value.type == f"{ERROR}.{code}"

    _run(rig, bus_address, scenario)


def test_reprocess_requeues_a_failed_meeting_and_it_runs_again(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed_failed("bad", 0)

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        assert await meetings.call_reprocess("bad") == "QUEUED"
        await eventually(lambda: rig.state("bad") == "DONE", "reprocessed")
        with pytest.raises(DBusError) as excinfo:
            await meetings.call_reprocess("nope")
        assert excinfo.value.type == f"{ERROR}.ENTITY_NOT_FOUND_MEETING"

    _run(rig, bus_address, scenario)

    assert rig.runner.stages_for("bad") == ["transcribe", "diarize"]


def test_reprocess_of_a_running_job_is_refused(tmp_path: Path, bus_address: str) -> None:
    gate = Gate()
    rig = ServiceRig(tmp_path, gate)
    rig.seed("busy")
    gate.hold("busy")

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        await eventually(gate.entered.is_set, "job running")
        with pytest.raises(DBusError) as excinfo:
            await meetings.call_reprocess("busy")
        assert excinfo.value.type == f"{ERROR}.MEETING_PROCESSING_ACTIVE"
        gate.release.set()
        await eventually(lambda: rig.state("busy") == "DONE", "job done")

    _run(rig, bus_address, scenario)


def test_properties_and_signals_follow_the_job(tmp_path: Path, bus_address: str) -> None:
    gate = Gate()
    rig = ServiceRig(tmp_path, gate)
    rig.seed("first", 0)
    rig.seed("second", 10)
    gate.hold("first")
    states: list[tuple[str, str]] = []
    finished: list[tuple[str, str, int]] = []
    changed: list[dict[str, object]] = []

    async def scenario(session: Session) -> None:
        meetings, client = session.meetings, session.client
        meetings.on_processing_state_changed(lambda i, s: states.append((i, s)))
        meetings.on_processing_finished(lambda i, s, n: finished.append((i, s, n)))
        introspection = await client.introspect(
            "dev.meetingscribe.Service", "/dev/meetingscribe/Service"
        )
        props: Proxy = client.get_proxy_object(
            "dev.meetingscribe.Service", "/dev/meetingscribe/Service", introspection
        ).get_interface("org.freedesktop.DBus.Properties")
        props.on_properties_changed(
            lambda iface, values, _: (
                changed.append({k: v.value for k, v in values.items()}) if iface == IFACE else None
            )
        )
        await eventually(gate.entered.is_set, "first job running")
        assert await meetings.get_active_meeting_id() == "first"
        assert await meetings.get_active_state() == "TRANSCRIBING"
        assert await meetings.get_queued_count() == 1

        gate.release.set()
        await eventually(lambda: len(finished) == 2, "both jobs finished")
        await eventually(lambda: any(c.get("ActiveState") == "IDLE" for c in changed), "idle")
        assert await meetings.get_active_meeting_id() == ""
        assert await meetings.get_active_state() == "IDLE"
        assert await meetings.get_queued_count() == 0

    _run(rig, bus_address, scenario)

    assert [s for i, s in states if i == "first"][-3:] == [
        "DIARIZING",
        "EXTRACTING_SAMPLES",
        "DONE",
    ]
    assert [s for i, s in states if i == "second"] == [
        "TRANSCRIBING",
        "DIARIZING",
        "EXTRACTING_SAMPLES",
        "DONE",
    ]
    assert finished == [("first", "DONE", 1), ("second", "DONE", 1)]  # in queue order
    assert {"ActiveMeetingId", "ActiveState", "QueuedCount"} <= {k for c in changed for k in c}


def test_a_finished_job_sends_one_notification_and_a_failed_one_says_so(
    tmp_path: Path, bus_address: str
) -> None:
    runner = ScriptedRunner()
    rig = ServiceRig(tmp_path, runner)
    rig.seed("good", 0)
    rig.seed("broken", 10)
    runner.behaviours[("broken", "diarize")] = lambda *_: Failure(
        WorkerCrashedError("The worker exited with code -9.")
    )
    finished: list[tuple[str, str, int]] = []

    async def scenario(session: Session) -> None:
        meetings = session.meetings
        meetings.on_processing_finished(lambda i, s, n: finished.append((i, s, n)))
        await eventually(lambda: rig.state("broken") == "FAILED", "second job failed")
        await eventually(lambda: len(rig.notifier.sent) == 2, "notifications sent")
        await meetings.call_reprocess("broken")  # a run we are subscribed for
        await eventually(lambda: len(finished) == 1, "failed signal")

    _run(rig, bus_address, scenario)

    assert [(n.summary, n.body) for n in rig.notifier.sent[:2]] == [
        ("Meeting processed: 1 speaker", ""),
        ("Meeting processing failed", "The worker exited with code -9."),
    ]
    assert finished == [("broken", "FAILED", 0)]  # the contracted FAILED payload


def test_notifications_can_be_turned_off(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, notify=False)
    rig.seed("quiet", 0)

    async def scenario(_: Session) -> None:
        await eventually(lambda: rig.state("quiet") == "DONE", "job done")

    _run(rig, bus_address, scenario)

    assert rig.notifier.sent == []
