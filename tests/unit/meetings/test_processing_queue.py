import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from returns.result import Failure

from meeting_scribe.meetings.domain.errors import WorkerCancelledError
from meeting_scribe.meetings.domain.meeting import RecordingInfo
from tests.unit.meetings.fakes import RECORDING, T0
from tests.unit.meetings.test_processing_controller import Fixture

WAIT = 5.0


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


@pytest.fixture
def served() -> Iterator[tuple[Fixture, threading.Thread]]:
    fx = Fixture()
    thread = threading.Thread(target=fx.controller.serve_forever, daemon=True)
    thread.start()
    yield fx, thread
    fx.controller.shutdown()
    thread.join(WAIT)
    assert not thread.is_alive()


def _block_until_cancelled(started: threading.Event, cancel_seen: threading.Event):
    def behaviour(job, on_event, cancel):
        started.set()
        cancel.wait(WAIT)
        cancel_seen.set()
        return Failure(WorkerCancelledError("stopped"))

    return behaviour


def test_jobs_run_one_at_a_time_in_queue_order(served: tuple[Fixture, threading.Thread]) -> None:
    fx, _ = served
    first = RecordingInfo(started_at=T0, stopped_at=T0 + timedelta(minutes=1))
    second = RecordingInfo(
        started_at=T0 + timedelta(minutes=5), stopped_at=T0 + timedelta(minutes=6)
    )

    fx.controller.preempt()  # hold the loop while both are queued
    fx.controller.enqueue("10-00", first).unwrap()
    fx.controller.enqueue("10-05", second).unwrap()
    fx.controller.release()
    _wait_for(lambda: len(fx.events.finished_ids) == 2)

    assert fx.events.finished_ids == ["10-00", "10-05"]
    assert [mid for mid, _, _ in fx.runner.calls] == ["10-00", "10-00", "10-05", "10-05"]
    assert fx.runner.max_active == 1


def test_shutdown_terminates_worker_and_keeps_job_resumable(
    served: tuple[Fixture, threading.Thread],
) -> None:
    fx, thread = served
    started, cancel_seen = threading.Event(), threading.Event()
    fx.runner.behaviours[("m1", "transcribe")] = _block_until_cancelled(started, cancel_seen)
    fx.controller.enqueue("m1", RECORDING).unwrap()
    assert started.wait(WAIT)

    fx.controller.shutdown()
    thread.join(WAIT)

    assert cancel_seen.is_set()
    assert not thread.is_alive()
    assert fx.state("m1") == "TRANSCRIBING"  # resumable at the next start
    assert fx.events.finished_ids == []
    assert fx.controller.queued_ids() == ()


def test_recording_preempts_gpu_work(served: tuple[Fixture, threading.Thread]) -> None:
    fx, _ = served
    started, cancel_seen = threading.Event(), threading.Event()
    blocked = _block_until_cancelled(started, cancel_seen)
    fx.runner.behaviours[("m1", "transcribe")] = blocked
    fx.controller.enqueue("m1", RECORDING).unwrap()
    assert started.wait(WAIT)

    fx.controller.preempt()  # a recording starts

    assert cancel_seen.wait(WAIT)
    _wait_for(lambda: fx.controller.queued_ids() == ("m1",))  # back at the queue head
    time.sleep(0.1)
    assert fx.state("m1") == "TRANSCRIBING"
    assert fx.runner.calls == [("m1", "transcribe", 900.0)]  # no worker while recording

    del fx.runner.behaviours[("m1", "transcribe")]
    fx.controller.release()  # the recording stopped
    _wait_for(lambda: fx.state("m1") == "DONE")

    assert fx.runner.stages_for("m1") == ["transcribe", "transcribe", "diarize"]
    assert fx.repository.meetings["m1"].processing.attempt == 1


def test_a_preempted_job_goes_back_before_the_jobs_queued_after_it(
    served: tuple[Fixture, threading.Thread],
) -> None:
    fx, _ = served
    started, cancel_seen = threading.Event(), threading.Event()
    fx.runner.behaviours[("first", "transcribe")] = _block_until_cancelled(started, cancel_seen)
    fx.controller.enqueue("first", RECORDING).unwrap()
    assert started.wait(WAIT)
    fx.controller.enqueue("second", RECORDING).unwrap()

    fx.controller.preempt()
    _wait_for(lambda: fx.controller.queued_ids() == ("first", "second"))

    assert cancel_seen.is_set()
    fx.runner.behaviours.pop(("first", "transcribe"))
    fx.controller.release()
    _wait_for(lambda: len(fx.events.finished_ids) == 2)
    assert fx.events.finished_ids == ["first", "second"]


def test_preempt_before_the_worker_spawns_never_starts_it() -> None:
    fx = Fixture()
    fx.controller.enqueue("m1", RECORDING).unwrap()
    fx.controller.preempt()

    assert fx.controller.run_next() is False
    assert fx.runner.calls == []
