"""Worker protocol v1 (docs/specs/processing.md, 6.2): job spec in, JSON-lines events out."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.meetings.domain.errors import WorkerProtocolError
from meeting_scribe.meetings.domain.meeting import AudioInfo, ProcessingParams, SpeakerEntry

PROTOCOL_VERSION = 1

type Stage = Literal["transcribe", "diarize"]


class WorkerJob(BaseModel):
    """What the service writes to the worker's stdin (then closes it)."""

    model_config = ConfigDict(frozen=True)

    protocol: int = PROTOCOL_VERSION
    stage: Stage
    meeting_dir: str
    device: str
    transcription: dict[str, JsonValue]
    speakers: dict[str, JsonValue]

    @classmethod
    def build(cls, stage: Stage, meeting_dir: str, device: str, params: ProcessingParams) -> Self:
        return cls(
            stage=stage,
            meeting_dir=meeting_dir,
            device=device,
            transcription=params.transcription,
            speakers=params.speakers,
        )

    def to_json(self) -> str:
        return self.model_dump_json()


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class StartedEvent(_Event):
    event: Literal["started"] = "started"
    pid: int
    device: str


class CacheHitEvent(_Event):
    event: Literal["cache_hit"] = "cache_hit"
    artifact: str


class ProgressEvent(_Event):
    event: Literal["progress"] = "progress"
    stage: str
    processed_seconds: float
    total_seconds: float


class StageEvent(_Event):
    event: Literal["stage"] = "stage"
    state: Literal["EXTRACTING_SAMPLES"]


class ResultEvent(_Event):
    event: Literal["result"] = "result"
    artifacts: dict[str, str] = Field(default_factory=dict)
    speakers: tuple[SpeakerEntry, ...] = ()
    detected_language: str = ""
    audio: AudioInfo | None = None


class WorkerErrorEvent(_Event):
    event: Literal["error"] = "error"
    error_code: str
    message: str


type WorkerEvent = (
    StartedEvent | CacheHitEvent | ProgressEvent | StageEvent | ResultEvent | WorkerErrorEvent
)
type ProgressOrNotice = StartedEvent | CacheHitEvent | ProgressEvent | StageEvent
"""The events a runner hands to its caller while the worker runs (``result``/``error`` end it)."""

_EVENTS: TypeAdapter[WorkerEvent] = TypeAdapter(
    Annotated[WorkerEvent, Field(discriminator="event")]
)


def parse_event(line: str) -> Result[WorkerEvent, WorkerProtocolError]:
    """One stdout line to one event; anything else is a protocol error (never raises)."""
    try:
        return Success(_EVENTS.validate_python(json.loads(line)))
    except (ValueError, ValidationError) as error:
        reason = "not JSON" if isinstance(error, json.JSONDecodeError) else "not a valid event"
        return Failure(WorkerProtocolError(f"Worker line is {reason}: {line[:200]!r}"))
