"""Applies one review command (rename, merge, unmerge) to the speakers of a meeting (spec 2).

Pure: no files and no ``meeting.json``. ``changed`` lets the caller skip the write and the signal
for a no-op (renaming to the same name, unmerging a speaker that is not merged).
"""

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.domain.review import (
    EffectiveSpeaker,
    ReviewState,
    effective_speakers,
    merge,
    rename,
    unmerge,
)


class ReviewOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ReviewState
    changed: bool


class ReviewSpeakers:
    def rename(
        self, state: ReviewState, speaker_id: str, name: str
    ) -> Result[ReviewOutcome, MeetingScribeError]:
        return _outcome(state, rename(state, speaker_id, name))

    def merge(
        self, state: ReviewState, source_id: str, target_id: str
    ) -> Result[ReviewOutcome, MeetingScribeError]:
        return _outcome(state, merge(state, source_id, target_id))

    def unmerge(
        self, state: ReviewState, speaker_id: str
    ) -> Result[ReviewOutcome, MeetingScribeError]:
        return _outcome(state, unmerge(state, speaker_id))

    def speakers(self, state: ReviewState) -> tuple[EffectiveSpeaker, ...]:
        return effective_speakers(state)


def _outcome(
    before: ReviewState, result: Result[ReviewState, MeetingScribeError]
) -> Result[ReviewOutcome, MeetingScribeError]:
    if isinstance(result, Failure):
        return result
    after = result.unwrap()
    return Success(ReviewOutcome(state=after, changed=after != before))
