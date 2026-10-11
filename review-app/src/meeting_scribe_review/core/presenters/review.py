"""The review pane of one meeting: speaker rows and transcript lines (AC-1, AC-8, AC-9, AC-11).

Replies are matched to the load that asked for them, so a late reply for a meeting the user already
left is dropped. Names and transcript text live only in the state; nothing is logged.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.errors import (
    SERVICE_NOT_RUNNING,
    ServiceError,
    message_for,
)
from meeting_scribe_review.core.formatting import format_duration
from meeting_scribe_review.core.gateway import MeetingsGateway
from meeting_scribe_review.core.models import Speaker, Utterance
from meeting_scribe_review.core.result import Err, Result
from meeting_scribe_review.core.states import (
    STATE_DONE,
    STATE_FAILED,
    STATE_UNSUPPORTED,
    is_processing,
    state_label,
)
from meeting_scribe_review.core.transcript import TranscriptLine, build_transcript

NO_SPEECH_TEXT = "No speech detected"
NO_SAMPLE_TEXT = "No sample"
NOT_PROCESSED_CODE = "MEETING_NOT_PROCESSED"


class ReviewStatus(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    READ_ONLY = "read_only"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SpeakerRow:
    id: str
    display: str
    detail: str  # the default label, shown beside a given name
    summary: str  # talk time and turns, merged speakers included
    is_me: bool
    sample_text: str  # "No sample" when there is none
    can_play: bool
    can_rename: bool
    can_merge: bool
    can_unmerge: bool
    merged_into_display: str


@dataclass(frozen=True, slots=True)
class ReviewState:
    meeting_id: str = ""
    status: ReviewStatus = ReviewStatus.IDLE
    banner: str = ""
    message: str = ""
    hint: str = ""
    empty_text: str = ""
    speakers: tuple[Speaker, ...] = ()
    active: tuple[SpeakerRow, ...] = ()
    merged: tuple[SpeakerRow, ...] = ()
    transcript: tuple[TranscriptLine, ...] = ()
    editable: bool = False


def _turns(count: int) -> str:
    return f"{count} turn" if count == 1 else f"{count} turns"


def _rows(speakers: Sequence[Speaker]) -> tuple[tuple[SpeakerRow, ...], tuple[SpeakerRow, ...]]:
    by_id = {speaker.id: speaker for speaker in speakers}
    active_count = sum(1 for speaker in speakers if speaker.is_active)
    rows = [
        SpeakerRow(
            id=speaker.id,
            display=speaker.display,
            detail=speaker.label if speaker.name else "",
            summary=f"{format_duration(speaker.talk_seconds)} · {_turns(speaker.utterance_count)}",
            is_me=speaker.is_me,
            sample_text="" if speaker.has_sample else NO_SAMPLE_TEXT,
            can_play=speaker.has_sample,
            can_rename=speaker.is_active,
            can_merge=speaker.is_active and not speaker.is_me and active_count >= 2,  # noqa: PLR2004
            can_unmerge=not speaker.is_active,
            merged_into_display=(
                by_id[speaker.merged_into].display if speaker.merged_into in by_id else ""
            ),
        )
        for speaker in speakers
    ]
    active = tuple(row for row, speaker in zip(rows, speakers, strict=True) if speaker.is_active)
    merged = tuple(
        row for row, speaker in zip(rows, speakers, strict=True) if not speaker.is_active
    )
    return active, merged


def _banner(state: str) -> str:
    if is_processing(state):
        return (
            f"This meeting is still processing ({state_label(state)}). "
            "Review opens when it finishes."
        )
    if state == STATE_FAILED:
        return "Processing this meeting failed. Reprocess it from the panel menu."
    if state == STATE_UNSUPPORTED:
        return "This meeting was saved by a newer version of Meeting Scribe."
    return "This meeting cannot be reviewed yet."


class ReviewPresenter:
    def __init__(self, gateway: MeetingsGateway, on_change: Callable[[ReviewState], None]) -> None:
        self._gateway = gateway
        self._on_change = on_change
        self._state = ReviewState()
        self._meeting_state = ""
        self._generation = 0

    @property
    def state(self) -> ReviewState:
        return self._state

    def open(self, meeting_id: str, meeting_state: str) -> None:
        """Show a meeting: its review when DONE, else a read-only banner for its state."""
        self._generation += 1  # replies still in flight belong to the previous meeting
        self._state = ReviewState(meeting_id=meeting_id)
        self._meeting_state = meeting_state
        if meeting_state == STATE_DONE:
            self._load(show_loading=True)
        else:
            self._read_only(_banner(meeting_state))

    def reload(self) -> None:
        if self._state.meeting_id and self._meeting_state == STATE_DONE:
            self._load(show_loading=True)

    def close(self) -> None:
        self._generation += 1
        self._meeting_state = ""
        self._publish(ReviewState())

    def apply_speakers(self, meeting_id: str, speakers: Sequence[Speaker]) -> None:
        """Take the rows an edit returned and refresh the transcript they change."""
        if meeting_id != self._state.meeting_id or self._state.status is not ReviewStatus.READY:
            return
        self._generation += 1
        generation = self._generation
        self._publish_ready(tuple(speakers), self._state.transcript)
        self._gateway.get_transcript(
            meeting_id,
            lambda result: self._transcript_loaded(generation, tuple(speakers), result),
        )

    def on_service_available(self, available: bool) -> None:
        if not self._state.meeting_id:
            return
        if available:
            self.reload()
        else:
            self._generation += 1
            self._fail(ServiceError(SERVICE_NOT_RUNNING))

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:
        if meeting_id != self._state.meeting_id:
            return
        self._meeting_state = state
        if state == STATE_DONE:
            self._load(show_loading=self._state.status is not ReviewStatus.READY)
        else:
            self._generation += 1
            self._read_only(_banner(state))

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:  # noqa: ARG002
        self.on_processing_state_changed(meeting_id, state)

    def on_speakers_changed(self, meeting_id: str) -> None:
        if meeting_id == self._state.meeting_id and self._meeting_state == STATE_DONE:
            self._load(show_loading=False)

    def _load(self, *, show_loading: bool) -> None:
        self._generation += 1
        generation = self._generation
        meeting_id = self._state.meeting_id
        if show_loading:
            self._publish(ReviewState(meeting_id=meeting_id, status=ReviewStatus.LOADING))
        self._gateway.get_speakers(
            meeting_id, lambda result: self._speakers_loaded(generation, meeting_id, result)
        )

    def _speakers_loaded(
        self, generation: int, meeting_id: str, result: Result[list[Speaker], ServiceError]
    ) -> None:
        if generation != self._generation:
            return
        if isinstance(result, Err):
            self._fail(result.error)
            return
        speakers = tuple(result.value)
        self._gateway.get_transcript(
            meeting_id,
            lambda transcript: self._transcript_loaded(generation, speakers, transcript),
        )

    def _transcript_loaded(
        self,
        generation: int,
        speakers: tuple[Speaker, ...],
        result: Result[list[Utterance], ServiceError],
    ) -> None:
        if generation != self._generation:
            return
        if isinstance(result, Err):
            self._fail(result.error)
            return
        self._publish_ready(speakers, tuple(build_transcript(speakers, result.value)))

    def _publish_ready(
        self, speakers: tuple[Speaker, ...], transcript: tuple[TranscriptLine, ...]
    ) -> None:
        active, merged = _rows(speakers)
        self._publish(
            ReviewState(
                meeting_id=self._state.meeting_id,
                status=ReviewStatus.READY,
                empty_text="" if speakers else NO_SPEECH_TEXT,
                speakers=speakers,
                active=active,
                merged=merged,
                transcript=transcript,
                editable=bool(active),
            )
        )

    def _read_only(self, banner: str) -> None:
        self._publish(
            ReviewState(
                meeting_id=self._state.meeting_id, status=ReviewStatus.READ_ONLY, banner=banner
            )
        )

    def _fail(self, error: ServiceError) -> None:
        message = message_for(error)
        if error.code == NOT_PROCESSED_CODE:
            self._read_only(message.text)
            return
        self._publish(
            ReviewState(
                meeting_id=self._state.meeting_id,
                status=ReviewStatus.ERROR,
                message=message.text,
                hint=message.hint,
            )
        )

    def _publish(self, state: ReviewState) -> None:
        self._state = state
        self._on_change(state)
