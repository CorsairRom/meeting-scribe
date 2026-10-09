import logging
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.recording.application.recording_controller import (
    RecordingController,
    RecordingStatus,
)
from meeting_scribe.recording.domain.errors import (
    AudioCaptureError,
    AudioDeviceNotFoundError,
    NoActiveRecordingError,
    RecordingAlreadyActiveError,
    RecordingStorageError,
)
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)

MEETINGS = Path("/meetings")


class Rig:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.resolver = FakeDeviceResolver()
        self.capture = FakeAudioCapture()
        self.storage = FakeRecordingStorage()

    def controller(self) -> RecordingController:
        return RecordingController(
            clock=self.clock,
            device_resolver=self.resolver,
            capture=self.capture,
            storage=self.storage,
            meetings_dir=MEETINGS,
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_status_is_idle_without_session(rig: Rig) -> None:
    assert rig.controller().status() == RecordingStatus(
        state="IDLE", session_id="", elapsed_seconds=0.0, directory="", failure_reason=""
    )


def test_start_runs_resolve_prepare_capture_and_reports_recording(rig: Rig) -> None:
    controller = rig.controller()

    result = controller.start()

    session = result.unwrap()
    assert session.state == "RECORDING"
    assert session.id == "2026-10-09_091500"
    assert rig.storage.prepared == [session]
    assert rig.capture.started_with is not None
    devices, audio_dir = rig.capture.started_with
    assert (devices.monitor_source, audio_dir) == ("fake.monitor", session.audio_dir)
    rig.clock.advance(42)
    assert controller.status() == RecordingStatus(
        state="RECORDING",
        session_id="2026-10-09_091500",
        elapsed_seconds=42.0,
        directory=str(MEETINGS / "2026-10-09_091500"),
        failure_reason="",
    )


def test_start_while_recording_fails_with_already_active(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()

    result = controller.start()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), RecordingAlreadyActiveError)
    assert len(rig.storage.prepared) == 1


def test_start_fails_when_devices_are_missing_and_leaves_state_idle(rig: Rig) -> None:
    rig.resolver.error = AudioDeviceNotFoundError("no mic")
    controller = rig.controller()

    result = controller.start()

    assert result == Failure(rig.resolver.error)
    assert controller.status().state == "IDLE"
    assert rig.capture.started_with is None


def test_start_fails_when_storage_fails_without_starting_capture(rig: Rig) -> None:
    rig.storage.error = RecordingStorageError("exists")
    controller = rig.controller()

    result = controller.start()

    assert result == Failure(rig.storage.error)
    assert controller.status().state == "IDLE"
    assert rig.capture.running is False


def test_start_capture_failure_marks_session_failed_and_returns_error(rig: Rig) -> None:
    rig.capture.start_error = AudioCaptureError("ffmpeg missing")
    controller = rig.controller()

    result = controller.start()

    assert result == Failure(rig.capture.start_error)
    status = controller.status()
    assert (status.state, status.failure_reason) == ("FAILED", "ffmpeg missing")
    assert status.session_id == "2026-10-09_091500"


def test_start_is_allowed_again_after_a_failed_session(rig: Rig) -> None:
    rig.capture.start_error = AudioCaptureError("x")
    controller = rig.controller()
    controller.start()
    rig.capture.start_error = None
    rig.clock.advance(10)

    result = controller.start()

    assert isinstance(result, Success)
    assert controller.status().state == "RECORDING"


def test_stop_without_recording_fails_with_no_active_recording(rig: Rig) -> None:
    result = rig.controller().stop()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), NoActiveRecordingError)
    assert rig.capture.stop_calls == 0


def test_stop_stops_capture_and_reports_stopped_with_frozen_elapsed(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    rig.clock.advance(60)

    session = controller.stop().unwrap()
    rig.clock.advance(500)

    assert session.state == "STOPPED"
    assert rig.capture.stop_calls == 1
    status = controller.status()
    assert (status.state, status.elapsed_seconds) == ("STOPPED", 60.0)


def test_stop_twice_fails_the_second_time(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    controller.stop()

    assert isinstance(controller.stop().failure(), NoActiveRecordingError)


def test_stop_capture_failure_marks_session_failed_and_returns_error(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    rig.capture.stop_error = AudioCaptureError("kill timeout")

    result = controller.stop()

    assert result == Failure(rig.capture.stop_error)
    status = controller.status()
    assert (status.state, status.failure_reason) == ("FAILED", "kill timeout")


def test_status_detects_crashed_capture_and_marks_failed(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    rig.clock.advance(30)
    rig.capture.crash()

    status = controller.status()

    assert status.state == "FAILED"
    assert status.failure_reason == "capture process exited unexpectedly"
    assert status.elapsed_seconds == 30.0
    rig.clock.advance(100)
    assert controller.status().elapsed_seconds == 30.0


def test_stop_after_crash_reports_failure_without_calling_status_first(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    rig.clock.advance(30)
    rig.capture.crash()

    result = controller.stop()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioCaptureError)
    status = controller.status()
    assert status.state == "FAILED"
    assert status.failure_reason == "capture process exited unexpectedly"
    assert rig.capture.stop_calls == 1  # still reaps the dead process


def test_start_after_crash_is_allowed_without_calling_status_first(rig: Rig) -> None:
    controller = rig.controller()
    controller.start()
    rig.capture.crash()
    rig.clock.advance(5)

    assert isinstance(controller.start(), Success)


def test_state_changes_and_failures_are_logged(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    controller = rig.controller()
    controller.start()
    rig.capture.stop_error = AudioCaptureError("kill timeout")
    controller.stop()

    levels = {record.levelno for record in caplog.records}
    assert logging.INFO in levels
    assert logging.WARNING in levels or logging.ERROR in levels
