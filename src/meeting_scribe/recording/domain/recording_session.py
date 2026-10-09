"""Recording session entity: one folder per meeting, one capture lifecycle."""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import AwareDatetime
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.entity import BaseEntity
from meeting_scribe.base.domain.errors import InvalidStateTransitionError

type RecordingState = Literal["RECORDING", "STOPPED", "FAILED"]

_ID_FORMAT = "%Y-%m-%d_%H%M%S"


class RecordingSession(BaseEntity):
    """``RECORDING`` -> ``STOPPED`` or ``FAILED``. Transitions return copies."""

    state: RecordingState
    started_at: AwareDatetime
    stopped_at: AwareDatetime | None = None
    directory: Path
    failure_reason: str | None = None

    @classmethod
    def begin(cls, meetings_dir: Path, now: datetime) -> Self:
        session_id = now.strftime(_ID_FORMAT)
        return cls(
            id=session_id,
            state="RECORDING",
            started_at=now,
            directory=meetings_dir / session_id,
        )

    @property
    def audio_dir(self) -> Path:
        return self.directory / "audio"

    def stop(self, now: datetime) -> Result[Self, InvalidStateTransitionError]:
        if self.state != "RECORDING":
            return Failure(self._invalid("stop"))
        return Success(self.model_copy(update={"state": "STOPPED", "stopped_at": now}))

    def fail(self, reason: str, now: datetime) -> Result[Self, InvalidStateTransitionError]:
        if self.state != "RECORDING":
            return Failure(self._invalid("fail"))
        return Success(
            self.model_copy(update={"state": "FAILED", "stopped_at": now, "failure_reason": reason})
        )

    def elapsed_seconds(self, now: datetime) -> float:
        end = self.stopped_at if self.stopped_at is not None else now
        return max((end - self.started_at).total_seconds(), 0.0)

    def _invalid(self, action: str) -> InvalidStateTransitionError:
        return InvalidStateTransitionError(
            f"Cannot {action} recording session '{self.id}' in state {self.state}."
        )


class _NullRecordingSession(RecordingSession):
    """Represents 'no session'."""

    _is_null_object: ClassVar[bool] = True
    id: str = ""
    state: RecordingState = "STOPPED"
    started_at: AwareDatetime = datetime.fromtimestamp(0, tz=UTC)
    directory: Path = Path()


NULL_RECORDING_SESSION = _NullRecordingSession()
