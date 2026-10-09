from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from returns.result import Failure, Success

from meeting_scribe.base.domain.errors import InvalidStateTransitionError
from meeting_scribe.recording.domain.audio_devices import AudioDevices
from meeting_scribe.recording.domain.recording_session import (
    NULL_RECORDING_SESSION,
    RecordingSession,
)

NOW = datetime(2026, 10, 9, 14, 30, 5, tzinfo=timezone(timedelta(hours=-5)))
MEETINGS = Path("/meetings")


def _begin() -> RecordingSession:
    return RecordingSession.begin(MEETINGS, NOW)


def test_begin_derives_id_and_directory_from_start_time() -> None:
    session = _begin()

    assert session.id == "2026-10-09_143005"
    assert session.directory == MEETINGS / "2026-10-09_143005"
    assert session.audio_dir == MEETINGS / "2026-10-09_143005" / "audio"
    assert (session.state, session.started_at, session.stopped_at) == ("RECORDING", NOW, None)
    assert session.failure_reason is None


def test_naive_start_time_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RecordingSession.begin(MEETINGS, datetime(2026, 10, 9, 14, 30, 5))  # noqa: DTZ001


def test_stop_moves_recording_to_stopped_without_mutating_original() -> None:
    session = _begin()
    later = NOW + timedelta(seconds=90)

    result = session.stop(later)

    stopped = result.unwrap()
    assert (stopped.state, stopped.stopped_at) == ("STOPPED", later)
    assert session.state == "RECORDING"


def test_fail_moves_recording_to_failed_with_reason() -> None:
    later = NOW + timedelta(seconds=5)

    failed = _begin().fail("boom", later).unwrap()

    assert (failed.state, failed.failure_reason, failed.stopped_at) == ("FAILED", "boom", later)


def test_invalid_transitions_return_failure_without_raising() -> None:
    stopped = _begin().stop(NOW).unwrap()
    failed = _begin().fail("x", NOW).unwrap()

    for result in (
        stopped.stop(NOW),
        stopped.fail("x", NOW),
        failed.stop(NOW),
        failed.fail("y", NOW),
    ):
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidStateTransitionError)


def test_elapsed_uses_now_while_recording_and_stopped_at_afterwards() -> None:
    session = _begin()
    stopped = session.stop(NOW + timedelta(seconds=60)).unwrap()

    assert session.elapsed_seconds(NOW + timedelta(seconds=30)) == 30.0
    assert stopped.elapsed_seconds(NOW + timedelta(hours=1)) == 60.0


def test_elapsed_is_never_negative() -> None:
    assert _begin().elapsed_seconds(NOW - timedelta(seconds=5)) == 0.0


def test_null_session_is_null_and_fails_as_result() -> None:
    error = InvalidStateTransitionError("none")

    assert NULL_RECORDING_SESSION.is_null is True
    assert NULL_RECORDING_SESSION.as_result(error) == Failure(error)
    assert _begin().is_null is False
    assert _begin().as_result(error) == Success(_begin())


def test_audio_devices_require_non_empty_sources() -> None:
    devices = AudioDevices(monitor_source="sink.monitor", mic_source="mic")
    assert (devices.monitor_source, devices.mic_source) == ("sink.monitor", "mic")
    for kwargs in (
        {"monitor_source": "", "mic_source": "m"},
        {"monitor_source": "s", "mic_source": ""},
    ):
        with pytest.raises(ValidationError):
            AudioDevices(**kwargs)


def test_utc_start_time_is_accepted() -> None:
    assert RecordingSession.begin(MEETINGS, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)).id == (
        "2026-01-02_030405"
    )
