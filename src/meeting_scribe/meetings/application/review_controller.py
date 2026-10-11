"""Speaker review of a finished meeting: rename, merge, unmerge and the derived transcript.

Every call is a read-modify-write of ``meeting.json`` under the processing lock and only on a
``DONE`` meeting. The write order is: save ``meeting.json`` with ``transcript_stale`` set, rewrite
``transcript.md``, save ``meeting.json`` with the flag cleared. A crash in between leaves the flag
set and ``repair_stale`` (service start) or the next call redoes the last two steps.
Errors never quote a speaker name.
"""

import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import (
    MeetingCorruptError,
    MeetingNotProcessedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.domain.meeting import Meeting, SpeakerReview
from meeting_scribe.meetings.domain.ports import MeetingLock, MeetingRepository
from meeting_scribe.speakers.application.render_transcript import RenderTranscript, Utterance
from meeting_scribe.speakers.application.review_speakers import (
    EffectiveSpeaker,
    ReviewOutcome,
    ReviewSpeakers,
    ReviewState,
    SpeakerRecord,
)

logger = logging.getLogger(__name__)

type _Command = Callable[[ReviewState], Result[ReviewOutcome, MeetingScribeError]]


class ReviewResult(BaseModel):
    """The refreshed speaker rows; ``changed`` is false for a no-op (no write, no signal)."""

    model_config = ConfigDict(frozen=True)

    speakers: tuple[EffectiveSpeaker, ...]
    changed: bool


class ReviewController:
    def __init__(
        self,
        repository: MeetingRepository,
        lock: MeetingLock,
        speakers: ReviewSpeakers,
        transcripts: RenderTranscript,
    ) -> None:
        self._repository = repository
        self._lock = lock
        self._speakers = speakers
        self._transcripts = transcripts

    def rename(
        self, meeting_id: str, speaker_id: str, name: str
    ) -> Result[ReviewResult, MeetingScribeError]:
        return self._edit(meeting_id, lambda state: self._speakers.rename(state, speaker_id, name))

    def merge(
        self, meeting_id: str, source_id: str, target_id: str
    ) -> Result[ReviewResult, MeetingScribeError]:
        return self._edit(
            meeting_id, lambda state: self._speakers.merge(state, source_id, target_id)
        )

    def unmerge(self, meeting_id: str, speaker_id: str) -> Result[ReviewResult, MeetingScribeError]:
        return self._edit(meeting_id, lambda state: self._speakers.unmerge(state, speaker_id))

    def speakers(self, meeting_id: str) -> Result[tuple[EffectiveSpeaker, ...], MeetingScribeError]:
        with self._lock.hold():
            return (
                self._done(meeting_id)
                .bind(self._state)
                .bind(self._speakers.speakers)
                .alt(self._at_boundary(meeting_id))
            )

    def transcript(self, meeting_id: str) -> Result[tuple[Utterance, ...], MeetingScribeError]:
        """The utterances with merges applied; repairs a stale ``transcript.md`` first."""
        with self._lock.hold():
            done = self._done(meeting_id).bind(self._repaired)
            if isinstance(done, Failure):
                return done
            meeting = done.unwrap()
            return (
                self._state(meeting)
                .bind(
                    lambda state: self._transcripts.effective_utterances(meeting.directory, state)
                )
                .alt(self._at_boundary(meeting_id))
            )

    def repair_stale(self) -> Result[int, MeetingStorageError]:
        """Redo the transcript of every ``DONE`` meeting left stale by a crash (service start)."""
        listed = self._repository.list_all()
        if isinstance(listed, Failure):
            return listed
        repaired = 0
        for meeting in listed.unwrap():
            if meeting.processing.state != "DONE" or not meeting.speaker_review.transcript_stale:
                continue
            with self._lock.hold():
                outcome = self._done(meeting.id).bind(self._repaired)
            if isinstance(outcome, Failure):
                logger.warning(
                    "Could not repair the transcript of meeting %s: %s",
                    meeting.id,
                    outcome.failure().error_code,
                )
                continue
            repaired += 1
        return Success(repaired)

    # -- internals (called with the lock held) --------------------------------------------

    def _edit(self, meeting_id: str, command: _Command) -> Result[ReviewResult, MeetingScribeError]:
        with self._lock.hold():
            return (
                self._done(meeting_id)
                .bind(self._repaired)
                .bind(lambda meeting: self._apply(meeting, command))
                .alt(self._at_boundary(meeting_id))
            )

    def _apply(
        self, meeting: Meeting, command: _Command
    ) -> Result[ReviewResult, MeetingScribeError]:
        state = self._state(meeting)
        if isinstance(state, Failure):
            return state
        outcome = command(state.unwrap())
        if isinstance(outcome, Failure):
            return outcome
        reviewed = outcome.unwrap()
        rows = self._speakers.speakers(reviewed.state)
        if isinstance(rows, Failure):
            return rows
        if not reviewed.changed:
            return Success(ReviewResult(speakers=rows.unwrap(), changed=False))
        saved = self._save(meeting, reviewed.state, stale=True)
        if isinstance(saved, Failure):
            return saved
        return self._rendered(saved.unwrap()).map(
            lambda _: ReviewResult(speakers=rows.unwrap(), changed=True)
        )

    def _shown(self, meeting: Meeting) -> Result[tuple[Utterance, ...], MeetingScribeError]:
        return self._state(meeting).bind(
            lambda state: self._transcripts.effective_utterances(meeting.directory, state)
        )

    def _repaired(self, meeting: Meeting) -> Result[Meeting, MeetingScribeError]:
        if not meeting.speaker_review.transcript_stale:
            return Success(meeting)
        return self._rendered(meeting)

    def _rendered(self, stale: Meeting) -> Result[Meeting, MeetingScribeError]:
        """Steps 2 and 3: rewrite ``transcript.md``, then clear the flag in ``meeting.json``."""
        state = self._state(stale)
        if isinstance(state, Failure):
            return state
        written = self._transcripts.run(stale.directory, state.unwrap())
        if isinstance(written, Failure):
            return written
        return self._save(stale, state.unwrap(), stale=False)

    def _save(
        self, meeting: Meeting, state: ReviewState, *, stale: bool
    ) -> Result[Meeting, MeetingScribeError]:
        names = {record.id: record.name for record in state.speakers}
        speakers = tuple(
            entry.model_copy(update={"name": names.get(entry.id, entry.name)})
            for entry in meeting.speakers
        )
        review = meeting.speaker_review.model_copy(
            update={
                "merged_into": dict(state.merged_into),
                "name_adopted_from": dict(state.name_adopted_from),
                "transcript_stale": stale,
            }
        )
        return self._repository.save(meeting.with_speaker_review(speakers, review))

    def _done(self, meeting_id: str) -> Result[Meeting, MeetingScribeError]:
        loaded = self._repository.get(meeting_id)
        if isinstance(loaded, Failure):
            return loaded
        found = loaded.unwrap().as_result(
            EntityNotFoundError(f"Meeting '{meeting_id}' does not exist.", detail_code="MEETING")
        )
        if isinstance(found, Failure):
            return found
        meeting = found.unwrap()
        if meeting.processing.state != "DONE":
            return Failure(
                MeetingNotProcessedError(
                    f"Meeting '{meeting_id}' is {meeting.processing.state}; "
                    "review needs a finished meeting."
                )
            )
        return Success(meeting)

    def _state(self, meeting: Meeting) -> Result[ReviewState, MeetingScribeError]:
        review: SpeakerReview = meeting.speaker_review
        state = ReviewState(
            speakers=tuple(
                SpeakerRecord(
                    id=entry.id,
                    label=entry.label,
                    name=entry.name,
                    is_me=entry.is_me,
                    talk_seconds=entry.talk_seconds,
                    utterance_count=entry.utterance_count,
                    sample=entry.sample,
                )
                for entry in meeting.speakers
            ),
            merged_into=dict(review.merged_into),
            name_adopted_from=dict(review.name_adopted_from),
        )
        return self._speakers.speakers(state).map(lambda _: state)

    @staticmethod
    def _at_boundary(meeting_id: str) -> Callable[[MeetingScribeError], MeetingScribeError]:
        """Map the speakers package's errors to the meetings ones (domains do not import)."""

        def convert(error: MeetingScribeError) -> MeetingScribeError:
            if error.error_code == "TRANSCRIPT_CORRUPT":
                return MeetingCorruptError(f"The speaker review data of '{meeting_id}' is damaged.")
            if error.error_code == "TRANSCRIPT_STORAGE_FAILED":
                return MeetingStorageError(f"The transcript of '{meeting_id}' could not be saved.")
            return error

        return convert
