"""Merge two speakers and undo it (AC-4, AC-5, AC-6). The service owns the rules."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.errors import ServiceError, message_for
from meeting_scribe_review.core.gateway import Callback, MeetingsGateway
from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.result import Err, Result

NAME_TAKEN_CODE = "SPEAKER_NAME_TAKEN"
NAME_CLASH_ON_UNMERGE = (
    "The name this speaker had is now used by another speaker. Rename one of them, then try again."
)


class MergeStatus(StrEnum):
    IDLE = "idle"
    CHOOSING = "choosing"
    BUSY = "busy"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MergeTarget:
    id: str
    display: str


@dataclass(frozen=True, slots=True)
class MergeState:
    status: MergeStatus = MergeStatus.IDLE
    meeting_id: str = ""
    source_id: str = ""
    targets: tuple[MergeTarget, ...] = ()
    message: str = ""
    hint: str = ""


class MergePresenter:
    """``on_applied(meeting_id, speakers)`` receives the refreshed rows of a successful call."""

    def __init__(
        self,
        gateway: MeetingsGateway,
        on_change: Callable[[MergeState], None],
        on_applied: Callable[[str, list[Speaker]], None],
    ) -> None:
        self._gateway = gateway
        self._on_change = on_change
        self._on_applied = on_applied
        self._state = MergeState()

    @property
    def state(self) -> MergeState:
        return self._state

    def start(self, meeting_id: str, source_id: str, speakers: Sequence[Speaker]) -> None:
        """Open the choice of a target; nothing opens for "Me", a merged speaker or no target."""
        source = next((s for s in speakers if s.id == source_id), None)
        if source is None or source.is_me or not source.is_active:
            return
        targets = tuple(
            MergeTarget(s.id, s.display) for s in speakers if s.is_active and s.id != source_id
        )
        if targets:
            self._publish(MergeState(MergeStatus.CHOOSING, meeting_id, source_id, targets))

    def confirm(self, target_id: str) -> None:
        state = self._state
        if state.status is MergeStatus.CHOOSING and any(t.id == target_id for t in state.targets):
            self.merge(state.meeting_id, state.source_id, target_id)

    def merge(self, meeting_id: str, source_id: str, target_id: str) -> None:
        if self._state.status is MergeStatus.BUSY:
            return
        self._publish(MergeState(MergeStatus.BUSY, meeting_id, source_id))
        self._gateway.merge_speakers(
            meeting_id, source_id, target_id, self._finished(meeting_id, source_id)
        )

    def unmerge(self, meeting_id: str, speaker_id: str) -> None:
        if self._state.status is MergeStatus.BUSY:
            return
        self._publish(MergeState(MergeStatus.BUSY, meeting_id, speaker_id))
        self._gateway.unmerge_speaker(
            meeting_id, speaker_id, self._finished(meeting_id, speaker_id, unmerging=True)
        )

    def cancel(self) -> None:
        self._publish(MergeState())

    def _finished(
        self, meeting_id: str, speaker_id: str, *, unmerging: bool = False
    ) -> Callback[list[Speaker]]:
        def done(result: Result[list[Speaker], ServiceError]) -> None:
            if isinstance(result, Err):
                self._fail(meeting_id, speaker_id, result.error, unmerging=unmerging)
                return
            self._publish(MergeState())
            self._on_applied(meeting_id, result.value)

        return done

    def _fail(
        self, meeting_id: str, speaker_id: str, error: ServiceError, *, unmerging: bool
    ) -> None:
        if unmerging and error.code == NAME_TAKEN_CODE:
            text, hint = NAME_CLASH_ON_UNMERGE, ""
        else:
            message = message_for(error)
            text, hint = message.text, message.hint
        self._publish(MergeState(MergeStatus.ERROR, meeting_id, speaker_id, (), text, hint))

    def _publish(self, state: MergeState) -> None:
        self._state = state
        self._on_change(state)
