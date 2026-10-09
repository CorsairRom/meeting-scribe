"""The recording -> processing hand-off, glue the composition root owns (spec section 1, OQ-1/3)."""

import logging
from datetime import timedelta

import pytest

from meeting_scribe.handoff import ProcessingRecordingListener
from meeting_scribe.recording.domain.recording_session import RecordingSession
from tests.unit.meetings.fakes import RECORDING, ROOT, T0, FakeNotifier
from tests.unit.meetings.test_processing_controller import Fixture

ID = "2026-10-09_101500"


def _session(state: str = "STOPPED", reason: str | None = None) -> RecordingSession:
    return RecordingSession(
        id=ID,
        state=state,  # type: ignore[arg-type]
        started_at=T0,
        stopped_at=None if state == "RECORDING" else T0 + timedelta(minutes=5),
        directory=ROOT / ID,
        failure_reason=reason,
    )


def test_a_stopped_session_is_enqueued_with_what_the_recording_knew() -> None:
    fx = Fixture()

    ProcessingRecordingListener(fx.controller).on_finished(_session())

    stored = fx.repository.meetings[ID]
    assert stored.processing.state == "QUEUED"
    assert (stored.recording.started_at, stored.recording.stopped_at) == (
        T0,
        T0 + timedelta(minutes=5),
    )
    assert stored.recording.state == "STOPPED"
    assert stored.directory == ROOT / ID


def test_a_failed_capture_is_enqueued_too_and_keeps_its_reason() -> None:
    fx = Fixture()

    ProcessingRecordingListener(fx.controller).on_finished(_session("FAILED", "ffmpeg died"))

    recording = fx.repository.meetings[ID].recording
    assert (recording.state, recording.failure_reason) == ("FAILED", "ffmpeg died")
    assert fx.state(ID) == "QUEUED"


def test_a_started_recording_holds_back_gpu_work_until_it_finishes() -> None:
    fx = Fixture()
    listener = ProcessingRecordingListener(fx.controller)
    fx.controller.enqueue("older", fx.seed("older", 0, "QUEUED").recording).unwrap()

    listener.on_started(_session("RECORDING"))
    assert fx.controller.run_next() is False  # recording: no worker may start
    assert fx.runner.calls == []

    listener.on_finished(_session())
    assert fx.controller.run_next() is True  # released: the queued job runs
    assert fx.state("older") == "DONE"


def test_a_session_that_cannot_be_enqueued_still_releases_the_gpu_and_logs_the_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fx = Fixture()
    listener = ProcessingRecordingListener(fx.controller)
    listener.on_started(_session("RECORDING"))
    fx.repository.fail_saves = True

    with caplog.at_level(logging.ERROR):
        listener.on_finished(_session())

    assert "MEETING_STORAGE_FAILED" in caplog.text
    assert ID in caplog.text
    fx.repository.fail_saves = False
    fx.controller.enqueue("later", RECORDING).unwrap()
    assert fx.controller.run_next() is True  # not stuck in "recording" mode


def test_the_gpu_is_released_even_when_queueing_raises() -> None:
    fx = Fixture()
    listener = ProcessingRecordingListener(fx.controller)
    listener.on_started(_session("RECORDING"))

    def explode(*_: object) -> None:
        raise RuntimeError("repository exploded")

    fx.controller.enqueue = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        listener.on_finished(_session())

    del fx.controller.enqueue  # the real method again
    fx.controller.enqueue("later", RECORDING).unwrap()
    assert fx.controller.run_next() is True  # not stuck in "recording" mode


def test_the_gpu_is_released_even_when_the_session_is_malformed() -> None:
    fx = Fixture()
    listener = ProcessingRecordingListener(fx.controller)
    listener.on_started(_session("RECORDING"))
    broken = _session().model_copy(update={"started_at": "not a date"})

    with pytest.raises(Exception):  # noqa: B017
        listener.on_finished(broken)

    fx.controller.enqueue("later", RECORDING).unwrap()
    assert fx.controller.run_next() is True


def test_a_session_that_cannot_be_enqueued_tells_the_user_without_meeting_content() -> None:
    fx = Fixture()
    notifier = FakeNotifier()
    listener = ProcessingRecordingListener(fx.controller, notifier=notifier)
    fx.repository.fail_saves = True

    listener.on_finished(_session())

    assert len(notifier.sent) == 1
    note = notifier.sent[0]
    assert note.summary == "Meeting could not be queued for processing"
    assert "restart" in note.body.lower()
    assert ID not in note.summary + note.body  # an id embeds the recording time: keep it out


def test_a_failed_notification_never_breaks_the_hand_off() -> None:
    from meeting_scribe.meetings.domain.errors import NotificationError  # noqa: PLC0415

    fx = Fixture()
    listener = ProcessingRecordingListener(
        fx.controller, notifier=FakeNotifier(error=NotificationError("no bus"))
    )
    listener.on_started(_session("RECORDING"))
    fx.repository.fail_saves = True

    listener.on_finished(_session())

    fx.repository.fail_saves = False
    fx.controller.enqueue("later", RECORDING).unwrap()
    assert fx.controller.run_next() is True  # the GPU was released


def test_a_queued_meeting_json_left_on_disk_is_picked_up_at_the_next_start() -> None:
    fx = Fixture()
    fx.seed(ID, 0, "QUEUED")  # meeting.json exists although the live enqueue reported a failure
    fresh = Fixture()
    fresh.repository.meetings = dict(fx.repository.meetings)

    assert fresh.controller.resume().unwrap() == 1
    assert fresh.controller.queued_ids() == (ID,)


def test_a_successful_enqueue_sends_no_notification() -> None:
    fx = Fixture()
    notifier = FakeNotifier()

    ProcessingRecordingListener(fx.controller, notifier=notifier).on_finished(_session())

    assert notifier.sent == []
