from datetime import datetime, timedelta
from pathlib import Path

from returns.result import Failure

from meeting_scribe.recording.domain.errors import RecordingStorageError
from meeting_scribe.recording.domain.recording_session import RecordingSession
from meeting_scribe.recording.infrastructure.filesystem_recording_storage import (
    FilesystemRecordingStorage,
)
from meeting_scribe.recording.infrastructure.system_clock import SystemClock
from tests.unit.recording.fakes import T0


def test_prepare_creates_session_and_audio_folders(tmp_path: Path) -> None:
    session = RecordingSession.begin(tmp_path / "meetings", T0)

    result = FilesystemRecordingStorage().prepare(session)

    assert result.unwrap() == session
    assert session.audio_dir.is_dir()


def test_prepare_fails_when_the_session_folder_already_exists(tmp_path: Path) -> None:
    session = RecordingSession.begin(tmp_path, T0)
    session.directory.mkdir()

    result = FilesystemRecordingStorage().prepare(session)

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), RecordingStorageError)
    assert not session.audio_dir.exists()


def test_prepare_fails_on_os_error(tmp_path: Path) -> None:
    blocker = tmp_path / "meetings"
    blocker.write_text("a file, not a directory")

    result = FilesystemRecordingStorage().prepare(RecordingSession.begin(blocker, T0))

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), RecordingStorageError)


def test_system_clock_is_timezone_aware_local_time() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert abs(now - datetime.now().astimezone()) < timedelta(seconds=5)
