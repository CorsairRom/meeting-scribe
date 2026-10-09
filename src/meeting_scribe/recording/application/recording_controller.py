"""Recording use cases: start, stop and status of the single in-progress recording."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError
from meeting_scribe.recording.domain.errors import (
    AudioCaptureError,
    NoActiveRecordingError,
    RecordingAlreadyActiveError,
)
from meeting_scribe.recording.domain.ports import (
    AudioCapture,
    AudioDeviceResolver,
    Clock,
    NullRecordingListener,
    RecordingListener,
    RecordingStorage,
)
from meeting_scribe.recording.domain.recording_session import (
    NULL_RECORDING_SESSION,
    RecordingSession,
)

logger = logging.getLogger(__name__)

CAPTURE_CRASHED_REASON = "capture process exited unexpectedly"


class RecordingStatus(BaseModel):
    """D-Bus friendly snapshot: no ``None`` values, empty strings mean 'absent'."""

    model_config = ConfigDict(frozen=True)

    state: Literal["IDLE", "RECORDING", "STOPPED", "FAILED"]
    session_id: str
    elapsed_seconds: float
    directory: str
    failure_reason: str


class RecordingController:
    """Holds the current session in memory (single-user service). Never raises."""

    def __init__(
        self,
        clock: Clock,
        device_resolver: AudioDeviceResolver,
        capture: AudioCapture,
        storage: RecordingStorage,
        meetings_dir: Path,
        listener: RecordingListener | None = None,
    ) -> None:
        self._clock = clock
        self._device_resolver = device_resolver
        self._capture = capture
        self._storage = storage
        self._meetings_dir = meetings_dir
        self._listener: RecordingListener = listener or NullRecordingListener()
        self._session: RecordingSession = NULL_RECORDING_SESSION

    def start(self) -> Result[RecordingSession, MeetingScribeError]:
        self._detect_crash()
        if self._session.state == "RECORDING" and not self._session.is_null:
            return Failure(
                RecordingAlreadyActiveError(f"Recording '{self._session.id}' is already active.")
            )
        devices = self._device_resolver.resolve()
        if isinstance(devices, Failure):
            return self._log_start_failure(devices)
        prepared = self._storage.prepare(
            RecordingSession.begin(self._meetings_dir, self._clock.now())
        )
        if isinstance(prepared, Failure):
            return self._log_start_failure(prepared)
        self._session = prepared.unwrap()
        started = self._capture.start(devices.unwrap(), self._session.audio_dir)
        if isinstance(started, Failure):
            self._fail(str(started.failure()))
            return self._log_start_failure(started)
        logger.info("Recording '%s' started.", self._session.id)
        self._announce(self._listener.on_started)
        return Success(self._session)

    def stop(self) -> Result[RecordingSession, MeetingScribeError]:
        if self._session.state != "RECORDING" or self._session.is_null:
            return Failure(NoActiveRecordingError("No recording is in progress."))
        if not self._capture.is_running():
            reason = self._fail_crashed()
            self._capture.stop()  # best effort: reap the dead process, the session already failed
            return Failure(AudioCaptureError(reason))
        match self._capture.stop():
            case Failure(error):
                self._fail(str(error))
                logger.error("Recording '%s' failed to stop: %s", self._session.id, error)
                self._announce(self._listener.on_finished)
                return Failure(error)
            case _:
                self._apply(self._session.stop(self._clock.now()))
                logger.info("Recording '%s' stopped.", self._session.id)
                self._announce(self._listener.on_finished)
                return Success(self._session)

    def status(self) -> RecordingStatus:
        self._detect_crash()
        session = self._session
        if session.is_null:
            return RecordingStatus(
                state="IDLE", session_id="", elapsed_seconds=0.0, directory="", failure_reason=""
            )
        return RecordingStatus(
            state=session.state,
            session_id=session.id,
            elapsed_seconds=session.elapsed_seconds(self._clock.now()),
            directory=str(session.directory),
            failure_reason=session.failure_reason or "",
        )

    def _fail(self, reason: str) -> None:
        self._apply(self._session.fail(reason, self._clock.now()))

    def _apply(self, transition: Result[RecordingSession, InvalidStateTransitionError]) -> None:
        """Adopt a transition; an invalid one is logged (never raised) and the session is kept."""
        match transition:
            case Success(session):
                self._session = session
            case Failure(error):
                logger.error(
                    "Recording '%s': state transition ignored: %s", self._session.id, error
                )

    def _detect_crash(self) -> None:
        if self._session.state == "RECORDING" and not self._capture.is_running():
            self._fail_crashed()

    def _fail_crashed(self) -> str:
        """Fail the session with the real exit cause (generic text if the adapter has none)."""
        reason = self._capture.exit_detail() or CAPTURE_CRASHED_REASON
        self._fail(reason)
        logger.error("Recording '%s': %s", self._session.id, reason)
        self._announce(self._listener.on_finished)
        return reason

    def _announce(self, hook: Callable[[RecordingSession], None]) -> None:
        """Tell the listener about the current session; its failure is logged, never raised."""
        try:
            hook(self._session)
        except Exception:
            logger.exception("Recording listener failed for session '%s'.", self._session.id)

    @staticmethod
    def _log_start_failure(failure: Failure[MeetingScribeError]) -> Failure[MeetingScribeError]:
        logger.warning("Recording failed to start: %s", failure.failure())
        return failure
