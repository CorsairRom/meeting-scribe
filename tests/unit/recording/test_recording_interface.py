"""RecordingInterface without a bus: members are called as plain Python (fakes only)."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from dbus_fast import DBusError

from meeting_scribe.recording.application.recording_controller import RecordingController
from meeting_scribe.recording.domain.errors import AudioCaptureError
from meeting_scribe.recording.infrastructure.dbus.recording_interface import RecordingInterface
from tests.unit.recording.fakes import (
    FakeAudioCapture,
    FakeClock,
    FakeDeviceResolver,
    FakeRecordingStorage,
)


class Rig:
    def __init__(self, capture: FakeAudioCapture | None = None) -> None:
        self.clock = FakeClock()
        self.capture = capture or FakeAudioCapture()
        self.controller = RecordingController(
            clock=self.clock,
            device_resolver=FakeDeviceResolver(),
            capture=self.capture,
            storage=FakeRecordingStorage(),
            meetings_dir=Path("/meetings"),
        )
        self.interface = RecordingInterface(self.controller, poll_interval=0.01)
        self.state_changed: list[tuple[str, str]] = []
        self.properties_changed: list[dict[str, Any]] = []
        # No bus is attached: capture what the interface would broadcast.
        self.interface.StateChanged = lambda state, session_id: self.state_changed.append(  # type: ignore[method-assign]
            (state, session_id)
        )
        self.interface.emit_properties_changed = lambda changed, *_: (  # type: ignore[method-assign]
            self.properties_changed.append(changed)
        )


def test_interface_name_is_versioned() -> None:
    assert Rig().interface.name == "dev.meetingscribe.Recording1"


def test_start_returns_the_session_id_and_exposes_it() -> None:
    rig = Rig()

    session_id = asyncio.run(rig.interface.start())

    assert session_id != ""
    assert rig.interface.State == "RECORDING"
    assert rig.interface.SessionId == session_id
    assert rig.interface.Directory != ""
    assert rig.interface.FailureReason == ""


def test_idle_properties_before_any_recording() -> None:
    interface = Rig().interface

    assert interface.State == "IDLE"
    assert interface.SessionId == ""
    assert interface.ElapsedSeconds == 0.0
    assert interface.Directory == ""
    assert interface.FailureReason == ""


def test_elapsed_seconds_follow_the_clock() -> None:
    rig = Rig()
    asyncio.run(rig.interface.start())

    rig.clock.advance(12)

    assert rig.interface.ElapsedSeconds == pytest.approx(12.0)


def test_second_start_raises_the_mapped_dbus_error() -> None:
    rig = Rig()
    asyncio.run(rig.interface.start())

    with pytest.raises(DBusError) as excinfo:
        asyncio.run(rig.interface.start())

    assert excinfo.value.type == "dev.meetingscribe.Error.RECORDING_ALREADY_ACTIVE"


def test_stop_returns_the_session_id_and_moves_to_stopped() -> None:
    rig = Rig()
    started = asyncio.run(rig.interface.start())

    stopped = asyncio.run(rig.interface.stop())

    assert stopped == started
    assert rig.interface.State == "STOPPED"


def test_stop_without_recording_raises_the_mapped_dbus_error() -> None:
    with pytest.raises(DBusError) as excinfo:
        asyncio.run(Rig().interface.stop())

    assert excinfo.value.type == "dev.meetingscribe.Error.NO_ACTIVE_RECORDING"


def test_failed_stop_keeps_the_error_message_and_reports_failed() -> None:
    rig = Rig(FakeAudioCapture(stop_error=AudioCaptureError("ffmpeg would not stop")))
    asyncio.run(rig.interface.start())

    with pytest.raises(DBusError) as excinfo:
        asyncio.run(rig.interface.stop())

    assert excinfo.value.type == "dev.meetingscribe.Error.AUDIO_CAPTURE_FAILED"
    assert excinfo.value.text == "ffmpeg would not stop"
    assert rig.interface.State == "FAILED"
    assert rig.interface.FailureReason == "ffmpeg would not stop"


def test_start_and_stop_emit_state_changes() -> None:
    rig = Rig()

    session_id = asyncio.run(rig.interface.start())
    asyncio.run(rig.interface.stop())

    assert rig.state_changed == [("RECORDING", session_id), ("STOPPED", session_id)]
    assert [set(changed) for changed in rig.properties_changed] == [
        {"State", "SessionId", "Directory", "FailureReason"}
    ] * 2
    assert rig.properties_changed[0]["State"] == "RECORDING"


def test_a_failed_call_that_changes_nothing_emits_nothing() -> None:
    rig = Rig()

    with pytest.raises(DBusError):
        asyncio.run(rig.interface.stop())

    assert rig.state_changed == []


def test_monitor_emits_failed_once_when_the_capture_crashes() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.interface.start()
        monitor = asyncio.create_task(rig.interface.monitor())
        rig.capture.crash("ffmpeg exited with code 1")
        await asyncio.sleep(0.1)
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(scenario())

    assert [state for state, _ in rig.state_changed] == ["RECORDING", "FAILED"]


def test_stop_if_active_stops_a_running_recording() -> None:
    rig = Rig()
    asyncio.run(rig.interface.start())

    asyncio.run(rig.interface.stop_if_active())

    assert rig.capture.stop_calls == 1
    assert rig.interface.State == "STOPPED"


def test_stop_if_active_is_a_noop_when_idle() -> None:
    rig = Rig()

    asyncio.run(rig.interface.stop_if_active())

    assert rig.capture.stop_calls == 0


def test_controller_calls_run_off_the_event_loop_thread() -> None:
    rig = Rig()
    threads: list[int] = []
    original = rig.controller.start

    def spy() -> Any:
        threads.append(threading.get_ident())
        return original()

    rig.controller.start = spy  # type: ignore[method-assign]

    asyncio.run(rig.interface.start())

    assert threads != [threading.get_ident()]
