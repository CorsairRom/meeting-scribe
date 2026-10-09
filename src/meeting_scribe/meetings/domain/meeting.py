"""Meeting entity: the recording facts plus the post-recording processing state machine.

The model mirrors ``meeting.json`` schema v1 (docs/specs/processing.md). Models accept extra keys
(``extra="allow"``) so that a rewrite preserves fields written by a newer minor version.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.entity import BaseEntity
from meeting_scribe.base.domain.errors import InvalidStateTransitionError
from meeting_scribe.meetings.domain.errors import MeetingProcessingActiveError

SCHEMA_VERSION = 1

type ProcessingState = Literal[
    "QUEUED",
    "TRANSCRIBING",
    "DIARIZING",
    "EXTRACTING_SAMPLES",
    "DONE",
    "FAILED",
    "UNSUPPORTED",
]
"""``UNSUPPORTED`` is not a pipeline state: it marks a ``meeting.json`` from a newer schema."""

IN_PROGRESS_STATES: tuple[ProcessingState, ...] = (
    "TRANSCRIBING",
    "DIARIZING",
    "EXTRACTING_SAMPLES",
)
UNFINISHED_STATES: tuple[ProcessingState, ...] = ("QUEUED", *IN_PROGRESS_STATES)
_TERMINAL: tuple[ProcessingState, ...] = ("DONE", "FAILED")


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class RecordingInfo(_Section):
    started_at: AwareDatetime
    stopped_at: AwareDatetime | None = None
    state: Literal["RECORDING", "STOPPED", "FAILED"] = "STOPPED"
    failure_reason: str = ""


class ProcessingFailure(_Section):
    stage: str
    error_code: str
    message: str


class ProcessingDevice(_Section):
    transcription: str = ""
    diarization: str = ""


class ProcessingParams(_Section):
    """Snapshot of the parameters a job ran with (cache keys are derived from it)."""

    transcription: dict[str, JsonValue] = Field(default_factory=dict)
    speakers: dict[str, JsonValue] = Field(default_factory=dict)


class AudioInfo(_Section):
    chunk_count: int
    total_frames: int
    duration_seconds: float


class SpeakerEntry(_Section):
    id: str
    label: str
    name: str = ""
    is_me: bool = False
    talk_seconds: float = 0.0
    utterance_count: int = 0
    sample: str = ""


class Processing(_Section):
    state: ProcessingState
    queued_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    attempt: int = 0
    failure: ProcessingFailure | None = None
    device: ProcessingDevice = Field(default_factory=ProcessingDevice)
    params: ProcessingParams = Field(default_factory=ProcessingParams)
    detected_language: str = ""
    audio: AudioInfo | None = None


class Meeting(BaseEntity):
    """``QUEUED`` -> ``TRANSCRIBING`` -> ``DIARIZING`` -> ``EXTRACTING_SAMPLES`` -> ``DONE``.

    Any non-terminal state can go to ``FAILED``; ``DONE`` and ``FAILED`` return to ``QUEUED`` on
    ``reprocess``. A job interrupted by a restart keeps its in-progress state on disk. Transitions
    return copies; ``now`` is passed in.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    directory: Path = Field(exclude=True)
    recording: RecordingInfo
    processing: Processing
    speakers: tuple[SpeakerEntry, ...] = ()
    files: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def enqueue(
        cls, meeting_id: str, directory: Path, recording: RecordingInfo, now: datetime
    ) -> Self:
        processing = Processing(state="QUEUED", queued_at=now, attempt=1)
        return cls(id=meeting_id, directory=directory, recording=recording, processing=processing)

    @classmethod
    def unsupported(cls, meeting_id: str, directory: Path) -> Self:
        """Placeholder for a ``meeting.json`` written by a newer schema: listed, never processed."""
        return cls(
            id=meeting_id,
            directory=directory,
            recording=RecordingInfo(started_at=datetime.fromtimestamp(0, tz=UTC)),
            processing=Processing(state="UNSUPPORTED"),
        )

    def start(
        self, now: datetime, params: ProcessingParams
    ) -> Result[Self, InvalidStateTransitionError]:
        return self._move(("QUEUED",), "TRANSCRIBING", {"started_at": now, "params": params})

    def resume(
        self, now: datetime, params: ProcessingParams
    ) -> Result[Self, InvalidStateTransitionError]:
        """Restart an interrupted job at the first worker; its caches skip finished work.

        Keeps ``queued_at`` (queue position) and ``attempt``: it is the same attempt.
        """
        return self._move(IN_PROGRESS_STATES, "TRANSCRIBING", {"started_at": now, "params": params})

    def transcription_done(self) -> Result[Self, InvalidStateTransitionError]:
        return self._move(("TRANSCRIBING",), "DIARIZING", {})

    def diarization_done(self) -> Result[Self, InvalidStateTransitionError]:
        return self._move(("DIARIZING",), "EXTRACTING_SAMPLES", {})

    def complete(
        self,
        now: datetime,
        speakers: tuple[SpeakerEntry, ...],
        files: dict[str, str],
        detected_language: str,
    ) -> Result[Self, InvalidStateTransitionError]:
        result = self._move(
            ("EXTRACTING_SAMPLES",),
            "DONE",
            {"finished_at": now, "failure": None, "detected_language": detected_language},
        )
        return result.map(
            lambda done: done.model_copy(update={"speakers": speakers, "files": files})
        )

    def fail(
        self, failure: ProcessingFailure, now: datetime
    ) -> Result[Self, InvalidStateTransitionError]:
        return self._move(UNFINISHED_STATES, "FAILED", {"finished_at": now, "failure": failure})

    def reprocess(self, now: datetime) -> Result[Self, InvalidStateTransitionError]:
        if self.processing.state in UNFINISHED_STATES:
            return Failure(
                MeetingProcessingActiveError(
                    f"Meeting '{self.id}' is {self.processing.state}; wait for it to finish."
                )
            )
        return self._move(
            _TERMINAL,
            "QUEUED",
            {
                "queued_at": now,
                "started_at": None,
                "finished_at": None,
                "failure": None,
                "attempt": self.processing.attempt + 1,
            },
        )

    def with_audio(self, audio: AudioInfo) -> Self:
        return self._with_processing({"audio": audio})

    def with_device(self, device: ProcessingDevice) -> Self:
        return self._with_processing({"device": device})

    def _move(
        self,
        allowed_from: tuple[ProcessingState, ...],
        to: ProcessingState,
        update: dict[str, object],
    ) -> Result[Self, InvalidStateTransitionError]:
        if self.processing.state not in allowed_from:
            return Failure(
                InvalidStateTransitionError(
                    f"Cannot move meeting '{self.id}' from {self.processing.state} to {to}."
                )
            )
        return Success(self._with_processing({**update, "state": to}))

    def _with_processing(self, update: dict[str, object]) -> Self:
        return self.model_copy(update={"processing": self.processing.model_copy(update=update)})


class _NullMeeting(Meeting):
    """Represents 'no meeting'. Reports ``UNSUPPORTED`` so every transition is refused."""

    _is_null_object: ClassVar[bool] = True
    id: str = ""
    directory: Path = Path()
    recording: RecordingInfo = RecordingInfo(started_at=datetime.fromtimestamp(0, tz=UTC))
    processing: Processing = Processing(state="UNSUPPORTED")


NULL_MEETING = _NullMeeting()
