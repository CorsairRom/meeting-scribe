"""Creates the on-disk folder of a recording session (ADR-004)."""

from returns.result import Failure, Result, Success

from meeting_scribe.recording.domain.errors import RecordingStorageError
from meeting_scribe.recording.domain.recording_session import RecordingSession


class FilesystemRecordingStorage:
    def prepare(self, session: RecordingSession) -> Result[RecordingSession, RecordingStorageError]:
        try:
            session.directory.mkdir(parents=True, exist_ok=False)
            session.audio_dir.mkdir(exist_ok=False)
        except OSError as error:
            return Failure(
                RecordingStorageError(
                    f"Cannot create session folder '{session.id}': {error.strerror or error}"
                )
            )
        return Success(session)
