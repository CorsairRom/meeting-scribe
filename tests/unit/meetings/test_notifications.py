import logging
from datetime import timedelta

import pytest

from meeting_scribe.meetings.application.event_hub import ProcessingEventHub
from meeting_scribe.meetings.application.notifications import NotifyOnFinish, build_notification
from meeting_scribe.meetings.application.processing_controller import ProcessingController
from meeting_scribe.meetings.domain.errors import NotificationError
from meeting_scribe.meetings.domain.meeting import (
    Meeting,
    ProcessingDevice,
    ProcessingFailure,
    ProcessingParams,
    SpeakerEntry,
)
from meeting_scribe.meetings.domain.notification import Notification
from meeting_scribe.meetings.domain.ports import ProcessingEvents
from tests.unit.meetings.fakes import (
    RECORDING,
    ROOT,
    T0,
    FakeClock,
    FakeNotifier,
    RecordingEvents,
)
from tests.unit.meetings.test_processing_controller import Fixture


def _speakers(count: int) -> tuple[SpeakerEntry, ...]:
    return tuple(SpeakerEntry(id=f"S{n}", label=f"Speaker {n}") for n in range(1, count + 1))


def _done(count: int, device: str = "cuda") -> Meeting:
    meeting = Meeting.enqueue("m1", ROOT / "m1", RECORDING, T0)
    meeting = meeting.start(T0, _params()).unwrap().transcription_done().unwrap()
    meeting = meeting.diarization_done().unwrap()
    meeting = meeting.with_device(ProcessingDevice(transcription=f"{device}/x", diarization=device))
    return meeting.complete(T0 + timedelta(minutes=1), _speakers(count), {}, "es").unwrap()


def _params() -> ProcessingParams:
    return ProcessingParams()


def _controller(fx: Fixture, events: ProcessingEvents) -> ProcessingController:
    return ProcessingController(
        repository=fx.repository,
        runner=fx.runner,
        audio=fx.audio,
        clock=FakeClock(),
        meetings_dir=ROOT,
        config=fx.config,
        events=events,
    )


def test_a_finished_meeting_says_how_many_speakers() -> None:
    assert build_notification(_done(3)) == Notification(
        summary="Meeting processed: 3 speakers", body=""
    )
    assert build_notification(_done(1)).summary == "Meeting processed: 1 speaker"


def test_silence_is_reported_as_no_speech() -> None:
    assert build_notification(_done(0)).summary == "Meeting processed: no speech detected"


def test_a_cpu_run_says_so() -> None:
    assert build_notification(_done(2, device="cpu")).summary == (
        "Meeting processed: 2 speakers (CPU)"
    )


def test_a_failed_meeting_carries_the_failure_message_and_nothing_else() -> None:
    failure = ProcessingFailure(
        stage="DIARIZING",
        error_code="DIARIZATION_MODEL_ACCESS_DENIED",
        message="Cannot access the model.\nSecond line with details.",
    )
    meeting = Meeting.enqueue("m1", ROOT / "m1", RECORDING, T0)
    failed = meeting.start(T0, _params()).unwrap().fail(failure, T0).unwrap()

    assert build_notification(failed) == Notification(
        summary="Meeting processing failed", body="Cannot access the model."
    )


def test_a_long_failure_message_is_cut_short() -> None:
    failure = ProcessingFailure(stage="QUEUED", error_code="X", message="word " * 100)
    failed = Meeting.enqueue("m1", ROOT / "m1", RECORDING, T0).fail(failure, T0).unwrap()

    body = build_notification(failed).body

    assert len(body) <= 200 and body.endswith("...")


def test_notification_on_finish_never_fails_the_job(caplog: pytest.LogCaptureFixture) -> None:
    fx = Fixture()
    notifier = FakeNotifier(error=NotificationError("no notification service"))
    controller = _controller(fx, NotifyOnFinish(notifier))
    controller.enqueue("m1", RECORDING).unwrap()

    with caplog.at_level(logging.WARNING):
        controller.run_next()

    assert fx.state("m1") == "DONE"  # the job is untouched by the notification failure
    assert [n.summary for n in notifier.sent] == ["Meeting processed: 1 speaker"]
    assert "NOTIFICATION_FAILED" in caplog.text


def test_a_notifier_that_raises_never_fails_the_job() -> None:
    class Exploding:
        def notify(self, notification: Notification):  # noqa: ARG002
            raise RuntimeError("bus is gone")

    fx = Fixture()
    controller = _controller(fx, NotifyOnFinish(Exploding()))  # type: ignore[arg-type]
    controller.enqueue("m1", RECORDING).unwrap()

    controller.run_next()

    assert fx.state("m1") == "DONE"


def test_the_hub_forwards_every_event_and_survives_a_broken_observer() -> None:
    fx = Fixture()
    first, second = RecordingEvents(fx.repository), RecordingEvents(fx.repository)

    class Broken(RecordingEvents):
        def state_changed(self, meeting: Meeting) -> None:  # noqa: ARG002
            raise RuntimeError("observer bug")

    hub = ProcessingEventHub()
    hub.subscribe(first)
    hub.subscribe(Broken(fx.repository))
    hub.subscribe(second)
    controller = _controller(fx, hub)
    controller.enqueue("m1", RECORDING).unwrap()

    controller.run_next()

    assert first.finished_ids == second.finished_ids == ["m1"]
    assert len(first.state_changes) == len(second.state_changes) > 0
    assert second.status_changes > 0
    hub.unsubscribe(second)
    heard = second.status_changes
    controller.enqueue("m2", RECORDING).unwrap()
    assert second.status_changes == heard  # no longer subscribed
    assert first.status_changes > heard
