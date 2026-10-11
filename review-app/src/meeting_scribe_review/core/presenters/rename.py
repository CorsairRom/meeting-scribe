"""Rename a speaker; a taken name offers to merge into that speaker or to edit it (OQ-3)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.errors import RENAME_CALL, ServiceError, message_for
from meeting_scribe_review.core.gateway import MeetingsGateway
from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.names import NameProblemKind, check_name, problem_message
from meeting_scribe_review.core.result import Err, Result


class RenameStatus(StrEnum):
    IDLE = "idle"
    EDITING = "editing"
    BUSY = "busy"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class NameConflict:
    """The name is used by ``display`` (``speaker_id``); merging into it is offered if allowed."""

    speaker_id: str
    display: str
    can_merge: bool


@dataclass(frozen=True, slots=True)
class RenameState:
    status: RenameStatus = RenameStatus.IDLE
    meeting_id: str = ""
    speaker_id: str = ""
    draft: str = ""
    problem: str = ""
    conflict: NameConflict | None = None


class RenamePresenter:
    """``on_applied`` gets the refreshed rows; ``request_merge(meeting, source, target)`` merges."""

    def __init__(
        self,
        gateway: MeetingsGateway,
        on_change: Callable[[RenameState], None],
        on_applied: Callable[[str, list[Speaker]], None],
        request_merge: Callable[[str, str, str], None],
    ) -> None:
        self._gateway = gateway
        self._on_change = on_change
        self._on_applied = on_applied
        self._request_merge = request_merge
        self._state = RenameState()
        self._speakers: tuple[Speaker, ...] = ()

    @property
    def state(self) -> RenameState:
        return self._state

    def start(self, meeting_id: str, speaker_id: str, speakers: Sequence[Speaker]) -> None:
        """Open the editor on an active speaker (the service refuses to rename a merged one)."""
        speaker = next((s for s in speakers if s.id == speaker_id), None)
        if speaker is None or not speaker.is_active:
            return
        self._speakers = tuple(speakers)
        self._publish(RenameState(RenameStatus.EDITING, meeting_id, speaker_id, speaker.name))

    def submit(self, raw: str) -> None:
        state = self._state
        if state.status is not RenameStatus.EDITING:
            return
        checked = check_name(raw, state.speaker_id, self._speakers)
        if isinstance(checked, Err):
            problem = checked.error
            if problem.kind is NameProblemKind.TAKEN:
                self._offer_conflict(raw, problem.speaker_id)
            else:
                self._publish(self._editing(raw, problem_message(problem)))
            return
        name = checked.value
        if name == self._current_name():
            self.cancel()
            return
        self._publish(RenameState(RenameStatus.BUSY, state.meeting_id, state.speaker_id, name))
        self._gateway.rename_speaker(
            state.meeting_id, state.speaker_id, name, lambda result: self._finished(name, result)
        )

    def merge_into_conflict(self) -> None:
        state = self._state
        conflict = state.conflict
        if state.status is RenameStatus.CONFLICT and conflict is not None and conflict.can_merge:
            self._publish(RenameState())
            self._request_merge(state.meeting_id, state.speaker_id, conflict.speaker_id)

    def edit_again(self) -> None:
        if self._state.status is RenameStatus.CONFLICT:
            self._publish(self._editing(self._state.draft))

    def cancel(self) -> None:
        self._publish(RenameState())

    def _current_name(self) -> str:
        return next((s.name for s in self._speakers if s.id == self._state.speaker_id), "")

    def _editing(self, draft: str, problem: str = "") -> RenameState:
        state = self._state
        return RenameState(RenameStatus.EDITING, state.meeting_id, state.speaker_id, draft, problem)

    def _offer_conflict(self, raw: str, other_id: str) -> None:
        state = self._state
        other = next(s for s in self._speakers if s.id == other_id)
        source = next(s for s in self._speakers if s.id == state.speaker_id)
        conflict = NameConflict(other.id, other.display, can_merge=not source.is_me)
        self._publish(
            RenameState(
                RenameStatus.CONFLICT, state.meeting_id, state.speaker_id, raw.strip(), "", conflict
            )
        )

    def _finished(self, name: str, result: Result[list[Speaker], ServiceError]) -> None:
        state = self._state
        if isinstance(result, Err):
            self._publish(self._editing(name, message_for(result.error, call=RENAME_CALL).text))
            return
        self._publish(RenameState())
        self._on_applied(state.meeting_id, result.value)

    def _publish(self, state: RenameState) -> None:
        self._state = state
        self._on_change(state)
