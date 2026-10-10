"""Processing wired into the running service: resume, preemption and shutdown (AC-4, 16, 17).

Everything is fake (runner, audio, capture, notifier); see ``tests/e2e/rig.py``.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.main import serve
from meeting_scribe.meetings.domain.errors import WorkerCancelledError
from meeting_scribe.recording.infrastructure.dbus.recording_interface import RecordingInterface
from tests.e2e.rig import ServiceRig, connect, eventually, interface, running
from tests.unit.meetings.fakes import ScriptedRunner, transcribe_ok

pytestmark = pytest.mark.dbus


class HoldFirstRun(ScriptedRunner):
    """The first transcribe run blocks until it is cancelled; later runs succeed at once."""

    def __init__(self) -> None:
        super().__init__()
        self.running = threading.Event()
        self.cancelled = threading.Event()
        self._first = True

    def behave(self, meeting_id: str) -> None:
        def hold(job, on_event, cancel):
            if not self._first:
                return transcribe_ok(job, on_event, cancel)
            self._first = False
            self.running.set()
            cancel.wait(5)
            self.cancelled.set()
            return Failure(WorkerCancelledError("stopped"))

        self.behaviours[(meeting_id, "transcribe")] = hold


def test_a_job_left_unfinished_resumes_when_the_service_starts(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path)
    rig.seed("m-old")

    async def main() -> None:
        async with running(rig, bus_address):
            await eventually(lambda: rig.state("m-old") == "DONE", "job done")

    asyncio.run(main())

    assert rig.runner.stages_for("m-old") == ["transcribe", "diarize"]


def test_shutdown_stops_the_running_worker_and_leaves_the_job_resumable(
    tmp_path: Path, bus_address: str
) -> None:
    runner = HoldFirstRun()
    rig = ServiceRig(tmp_path, runner)
    rig.seed("m-old")
    runner.behave("m-old")

    async def main() -> None:
        async with running(rig, bus_address):
            await eventually(runner.running.is_set, "worker running")
        # leaving the block stopped the service: serve() returned 0 within its bound

    asyncio.run(main())

    assert runner.cancelled.is_set()  # the worker was told to stop before the service exited
    assert rig.state("m-old") == "TRANSCRIBING"  # still in progress on disk: resumes next start


def test_a_recording_preempts_the_running_job_and_it_resumes_after_stop(
    tmp_path: Path, bus_address: str
) -> None:
    runner = HoldFirstRun()
    rig = ServiceRig(tmp_path, runner)
    rig.seed("m-old")
    runner.behave("m-old")

    async def main() -> None:
        async with running(rig, bus_address):
            client = await connect(bus_address)
            try:
                recording = await interface(client, "dev.meetingscribe.Recording1")
                await eventually(runner.running.is_set, "worker running")

                session = await recording.call_start()  # the D-Bus loop is not blocked by the job

                await eventually(runner.cancelled.is_set, "worker preempted")
                await asyncio.sleep(0.3)
                assert len(runner.calls) == 1  # no worker started while recording
                assert rig.state("m-old") == "TRANSCRIBING"  # back at the head, state kept

                assert await recording.call_stop() == session
                await eventually(lambda: rig.state("m-old") == "DONE", "old job done")
                await eventually(lambda: rig.state(session) == "DONE", "new meeting done")
            finally:
                client.disconnect()

    asyncio.run(main())

    assert [stage for mid, stage, _ in runner.calls if mid == "m-old"] == [
        "transcribe",  # preempted
        "transcribe",  # resumed from the head of the queue
        "diarize",
    ]
    assert isinstance(rig.repository.get("m-old"), Success)


def test_a_failure_while_starting_still_stops_the_processing_loop(
    tmp_path: Path, bus_address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ServiceRig(tmp_path)

    def boom(_: RecordingInterface) -> None:
        raise RuntimeError("monitor failed")

    monkeypatch.setattr(RecordingInterface, "start_monitor", boom)
    stopped: list[bool] = []
    real_shutdown = rig.app.processing.shutdown
    monkeypatch.setattr(
        rig.app.processing, "shutdown", lambda: (stopped.append(True), real_shutdown())[1]
    )

    with pytest.raises(RuntimeError, match="monitor failed"):
        asyncio.run(serve(rig.app, bus_address=bus_address, poll_interval=0.05))

    assert stopped == [True]


def test_a_processing_loop_that_outlives_the_shutdown_bound_exits_with_a_distinct_code(
    tmp_path: Path, bus_address: str
) -> None:
    from meeting_scribe.main import EXIT_PROCESSING_STUCK  # noqa: PLC0415

    rig = ServiceRig(tmp_path)
    release = threading.Event()
    rig.app.processing.serve_forever = lambda: release.wait(5)  # type: ignore[method-assign]

    async def main() -> int:
        ready, stop = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(
            serve(rig.app, bus_address=bus_address, ready=ready, stop=stop, shutdown_timeout=0.05)
        )
        await asyncio.wait_for(ready.wait(), timeout=5)
        stop.set()
        return await asyncio.wait_for(task, timeout=5)

    try:
        exit_code = asyncio.run(main())
    finally:
        release.set()

    assert exit_code == EXIT_PROCESSING_STUCK == 4
