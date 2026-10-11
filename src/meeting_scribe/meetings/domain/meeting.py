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


class SpeakerReview(_Section):
    """What the user did in the review app; cleared whenever the speakers are recomputed.

    ``merged_into`` is ``source id -> target id`` and ``name_adopted_from`` is ``target id ->
    source id``. ``transcript_stale`` is set while ``transcript.md`` may not match the saved
    names (spec: write order meeting.json, transcript.md, clear the flag).
    """

    merged_into: dict[str, str] = Field(default_factory=dict)
    name_adopted_from: dict[str, str] = Field(default_factory=dict)
    transcript_stale: bool = False

    def stale(self) -> Self:
        return self.model_copy(update={"transcript_stale": True})

    def fresh(self) -> Self:
        return self.model_copy(update={"transcript_stale": False})


class SummaryFailure(_Section):
    """The last failed attempt: a stable code and when; never content."""

    error_code: str
    at: AwareDatetime


class SummaryInfo(_Section):
    """What ``meeting.json`` knows about ``summary.md`` (spec 7).

    ``file`` is empty while only a failure was recorded (no summary was ever saved).
    ``input_fingerprint`` is that of the transcript the summary used: staleness is computed
    against the current one, never stored.
    """

    file: str = ""
    generated_at: AwareDatetime | None = None
    provider: str = ""
    cloud: bool = False
    model: str = ""
    language: str = ""
    title: str = ""
    input_fingerprint: str = ""
    chunks: int = 0
    prompt_version: int = 0
    last_failure: SummaryFailure | None = None


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
    speaker_review: SpeakerReview = Field(default_factory=SpeakerReview)
    files: dict[str, str] = Field(default_factory=dict)
    summary: SummaryInfo | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def active_speaker_count(self) -> int:
        """Speakers that are not merged into another one."""
        return sum(
            1 for speaker in self.speakers if speaker.id not in self.speaker_review.merged_into
        )

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
        kept = {"summary": self.summary.file} if self.summary and self.summary.file else {}
        return result.map(
            lambda done: done.model_copy(
                update={
                    "speakers": speakers,
                    "files": {**files, **kept},
                    "speaker_review": SpeakerReview(),
                }
            )
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
        moved = self._move(
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
        return moved.map(
            lambda queued: queued.model_copy(update={"speaker_review": SpeakerReview()})
        )

    def with_speaker_review(
        self, speakers: tuple[SpeakerEntry, ...], review: SpeakerReview
    ) -> Self:
        """A copy with the reviewed speakers (names) and the review state; not a state change."""
        return self.model_copy(update={"speakers": speakers, "speaker_review": review})

    def with_summary(self, info: SummaryInfo) -> Self:
        """A copy with a freshly saved summary; clears the last failure. Not a state change."""
        return self.model_copy(
            update={"summary": info, "files": {**self.files, "summary": info.file}}
        )

    def with_summary_failure(self, error_code: str, at: datetime) -> Self:
        """A copy that remembers the last failed attempt; a saved summary stays as it was."""
        failure = SummaryFailure(error_code=error_code, at=at)
        info = (self.summary or SummaryInfo()).model_copy(update={"last_failure": failure})
        return self.model_copy(update={"summary": info})

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
