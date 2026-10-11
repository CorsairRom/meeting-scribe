import logging

import pytest
from returns.result import Failure

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    MeetingScribeError,
)
from meeting_scribe.meetings.application.review_controller import ReviewController
from meeting_scribe.meetings.domain.errors import (
    MeetingCorruptError,
    MeetingNotProcessedError,
    MeetingStorageError,
)
from meeting_scribe.meetings.domain.meeting import (
    Meeting,
    ProcessingFailure,
    ProcessingParams,
    SpeakerEntry,
    SpeakerReview,
)
from meeting_scribe.speakers.application.render_transcript import RenderTranscript, Utterance
from meeting_scribe.speakers.application.review_speakers import ReviewSpeakers
from meeting_scribe.speakers.domain.errors import (
    SpeakerNameTakenError,
    TranscriptCorruptError,
    TranscriptStorageError,
)
from tests.unit.meetings.fakes import (
    RECORDING,
    ROOT,
    T0,
    FakeMeetingLock,
    FakeUtterancesStore,
    InMemoryMeetingRepository,
)

MEETING_ID = "m1"
SPEAKERS = (
    SpeakerEntry(id="ME", label="Me", is_me=True, talk_seconds=5.0, utterance_count=1),
    SpeakerEntry(id="S1", label="Speaker 1", talk_seconds=30.0, utterance_count=3),
    SpeakerEntry(id="S2", label="Speaker 2", talk_seconds=20.0, utterance_count=2),
)
UTTERANCES = (
    Utterance(speaker="S1", start=0.0, end=2.0, text="Fake line one."),
    Utterance(speaker="S2", start=2.0, end=4.0, text="Fake line two."),
)


class Rig:
    def __init__(self) -> None:
        self.repository = InMemoryMeetingRepository()
        self.lock = FakeMeetingLock()
        self.repository.lock = self.lock
        self.store = FakeUtterancesStore(self.repository, self.lock)
        self.store.utterances = UTTERANCES
        self.controller = ReviewController(
            self.repository, self.lock, ReviewSpeakers(), RenderTranscript(self.store)
        )

    def add(self, meeting_id: str = MEETING_ID, review: SpeakerReview | None = None) -> Meeting:
        directory = ROOT / meeting_id
        meeting = (
            Meeting.enqueue(meeting_id, directory, RECORDING, T0)
            .start(T0, _params())
            .unwrap()
            .transcription_done()
            .unwrap()
            .diarization_done()
            .unwrap()
            .complete(T0, SPEAKERS, {}, "en")
            .unwrap()
        )
        meeting = meeting.with_speaker_review(SPEAKERS, review or SpeakerReview())
        self.repository.meetings[meeting_id] = meeting
        return meeting

    def stored(self, meeting_id: str = MEETING_ID) -> Meeting:
        return self.repository.meetings[meeting_id]


def _params() -> ProcessingParams:
    return ProcessingParams()


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_rename_saves_the_name_and_returns_the_refreshed_rows(rig: Rig) -> None:
    rig.add()

    result = rig.controller.rename(MEETING_ID, "S1", "  Fake Ana ").unwrap()

    assert result.changed is True
    assert [s.display for s in result.speakers][:2] == ["Me", "Fake Ana"]
    assert rig.stored().speakers[1].name == "Fake Ana"
    assert "Fake Ana" in rig.store.transcripts[-1]


def test_the_write_order_is_stale_save_then_transcript_then_clear(rig: Rig) -> None:
    rig.add()

    rig.controller.rename(MEETING_ID, "S1", "Fake Ana").unwrap()

    assert rig.store.stale_when_written == [True]
    assert rig.stored().speaker_review.transcript_stale is False
    assert len(rig.repository.saves) == 2


def test_edits_run_under_the_lock(rig: Rig) -> None:
    rig.add()

    rig.controller.rename(MEETING_ID, "S1", "Fake Ana").unwrap()

    assert rig.repository.lock_held_on_save == [True, True]
    assert rig.store.lock_held_when_written == [True]
    assert rig.lock.held is False


@pytest.mark.parametrize("state_steps", [0, 1, 2])
def test_edits_are_refused_unless_the_meeting_is_done(rig: Rig, state_steps: int) -> None:
    meeting = Meeting.enqueue(MEETING_ID, ROOT / MEETING_ID, RECORDING, T0)
    if state_steps:
        meeting = meeting.start(T0, _params()).unwrap()
    if state_steps == 2:
        meeting = meeting.fail(_failure(), T0).unwrap()
    rig.repository.meetings[MEETING_ID] = meeting

    result = rig.controller.rename(MEETING_ID, "S1", "Fake Ana")

    error = result.failure()
    assert isinstance(error, MeetingNotProcessedError)
    assert isinstance(error, InvalidStateTransitionError)
    assert rig.repository.saves == []
    assert rig.lock.holds == 1


def _failure() -> ProcessingFailure:
    return ProcessingFailure(stage="DIARIZING", error_code="X", message="x")


def test_an_unknown_meeting_is_not_found(rig: Rig) -> None:
    error = rig.controller.rename("nope", "S1", "Fake Ana").failure()

    assert isinstance(error, EntityNotFoundError)
    assert error.error_code == "ENTITY_NOT_FOUND_MEETING"


def test_repository_read_failures_pass_through(rig: Rig) -> None:
    rig.repository.get_failures[MEETING_ID] = MeetingStorageError("io")

    assert isinstance(
        rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure(), MeetingStorageError
    )


def test_a_rule_failure_changes_nothing(rig: Rig) -> None:
    rig.add()

    error = rig.controller.rename(MEETING_ID, "S1", "Speaker 2").failure()

    assert isinstance(error, SpeakerNameTakenError)
    assert rig.repository.saves == []
    assert rig.store.transcripts == []


def test_a_no_op_edit_writes_nothing(rig: Rig) -> None:
    rig.add()

    result = rig.controller.unmerge(MEETING_ID, "S1").unwrap()

    assert result.changed is False
    assert rig.repository.saves == []
    assert rig.store.transcripts == []


def test_merge_and_unmerge_round_trip_through_meeting_json(rig: Rig) -> None:
    rig.add()

    merged = rig.controller.merge(MEETING_ID, "S2", "S1").unwrap()
    assert rig.stored().speaker_review.merged_into == {"S2": "S1"}
    assert rig.stored().active_speaker_count == 2
    assert [s.id for s in merged.speakers if not s.merged_into] == ["ME", "S1"]

    rig.controller.unmerge(MEETING_ID, "S2").unwrap()
    assert rig.stored().speaker_review.merged_into == {}


def test_extra_keys_on_speaker_entries_survive_an_edit(rig: Rig) -> None:
    rig.add()
    extended = SpeakerEntry.model_validate({**SPEAKERS[1].model_dump(), "future_key": 7})
    meeting = rig.stored()
    rig.repository.meetings[MEETING_ID] = meeting.with_speaker_review(
        (SPEAKERS[0], extended, SPEAKERS[2]), meeting.speaker_review
    )

    rig.controller.rename(MEETING_ID, "S1", "Fake Ana").unwrap()

    assert rig.stored().speakers[1].model_dump()["future_key"] == 7


def test_an_impossible_review_state_maps_to_meeting_corrupt(rig: Rig) -> None:
    rig.add(review=SpeakerReview(merged_into={"S9": "S1"}))

    error = rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure()

    assert isinstance(error, MeetingCorruptError)
    assert error.error_code == "MEETING_CORRUPT"
    assert rig.repository.saves == []


def test_a_damaged_utterances_file_maps_to_meeting_corrupt(rig: Rig) -> None:
    rig.add()
    rig.store.load_failure = TranscriptCorruptError("damaged")

    error = rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure()

    assert error.error_code == "MEETING_CORRUPT"  # type: ignore[union-attr]
    assert isinstance(error, MeetingCorruptError)


def test_a_missing_utterances_file_passes_through(rig: Rig) -> None:
    rig.add()
    rig.store.load_failure = EntityNotFoundError("none", detail_code="TRANSCRIPT")

    error = rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure()

    assert error.error_code == "ENTITY_NOT_FOUND_TRANSCRIPT"  # type: ignore[union-attr]


def test_a_transcript_write_failure_keeps_the_saved_edit_and_the_stale_flag(rig: Rig) -> None:
    rig.add()
    rig.store.write_failure = TranscriptStorageError("io")

    error = rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure()

    assert error.error_code == "MEETING_STORAGE_FAILED"  # type: ignore[union-attr]
    assert isinstance(error, MeetingStorageError)
    assert rig.stored().speakers[1].name == "Fake Ana"
    assert rig.stored().speaker_review.transcript_stale is True


def test_a_failure_clearing_the_flag_fails_the_call_but_keeps_the_edit(rig: Rig) -> None:
    rig.add()
    rig.repository.fail_on_save_number = 2

    error = rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure()

    assert isinstance(error, MeetingStorageError)
    assert rig.stored().speaker_review.transcript_stale is True
    assert rig.stored().speakers[1].name == "Fake Ana"


def test_a_failure_of_the_first_save_writes_no_transcript(rig: Rig) -> None:
    rig.add()
    rig.repository.fail_saves = True

    assert isinstance(
        rig.controller.rename(MEETING_ID, "S1", "Fake Ana").failure(), MeetingStorageError
    )
    assert rig.store.transcripts == []


def test_stale_transcript_is_repaired(rig: Rig) -> None:
    stale = SpeakerReview(transcript_stale=True)
    rig.add(review=stale)
    rig.add("m2")  # fresh: left alone
    rig.add("m3")
    rig.repository.meetings["m3"] = Meeting.enqueue("m3", ROOT / "m3", RECORDING, T0)

    repaired = rig.controller.repair_stale().unwrap()

    assert repaired == 1
    assert rig.stored().speaker_review.transcript_stale is False
    assert rig.store.stale_when_written == [True]
    assert rig.store.lock_held_when_written == [True]


def test_repair_goes_on_after_one_meeting_fails(rig: Rig) -> None:
    rig.add("a", review=SpeakerReview(transcript_stale=True))
    rig.add("b", review=SpeakerReview(transcript_stale=True))
    rig.repository.fail_saves_for = {"a"}

    assert rig.controller.repair_stale().unwrap() == 1
    assert rig.stored("b").speaker_review.transcript_stale is False
    assert rig.stored("a").speaker_review.transcript_stale is True


def test_repair_fails_only_when_the_meetings_cannot_be_listed(rig: Rig) -> None:
    class Broken(InMemoryMeetingRepository):
        def list_all(self):  # type: ignore[no-untyped-def]
            return Failure(MeetingStorageError("io"))

    controller = ReviewController(Broken(), rig.lock, ReviewSpeakers(), RenderTranscript(rig.store))

    assert isinstance(controller.repair_stale().failure(), MeetingStorageError)


def test_an_edit_repairs_a_stale_transcript_first(rig: Rig) -> None:
    rig.add(review=SpeakerReview(transcript_stale=True))

    rig.controller.unmerge(MEETING_ID, "S1").unwrap()  # no-op edit

    assert rig.stored().speaker_review.transcript_stale is False
    assert len(rig.store.transcripts) == 1


def test_get_transcript_repairs_then_serves_utterances_with_merges(rig: Rig) -> None:
    rig.add(review=SpeakerReview(merged_into={"S2": "S1"}, transcript_stale=True))

    shown = rig.controller.transcript(MEETING_ID).unwrap()

    assert [u.speaker for u in shown] == ["S1", "S1"]
    assert rig.stored().speaker_review.transcript_stale is False
    assert rig.lock.holds >= 1


def test_get_transcript_needs_a_done_meeting(rig: Rig) -> None:
    rig.repository.meetings[MEETING_ID] = Meeting.enqueue(
        MEETING_ID, ROOT / MEETING_ID, RECORDING, T0
    )

    assert isinstance(rig.controller.transcript(MEETING_ID).failure(), MeetingNotProcessedError)


def test_get_speakers_lists_active_speakers_first(rig: Rig) -> None:
    rig.add(review=SpeakerReview(merged_into={"S2": "S1"}))

    rows = rig.controller.speakers(MEETING_ID).unwrap()

    assert [(r.id, r.merged_into) for r in rows] == [
        ("ME", ""),
        ("S1", ""),
        ("S2", "S1"),
    ]
    assert rows[1].utterance_count == 5


def test_get_speakers_needs_a_done_meeting(rig: Rig) -> None:
    rig.repository.meetings[MEETING_ID] = Meeting.enqueue(
        MEETING_ID, ROOT / MEETING_ID, RECORDING, T0
    )

    assert isinstance(rig.controller.speakers(MEETING_ID).failure(), MeetingNotProcessedError)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (TranscriptStorageError("io"), "MEETING_STORAGE_FAILED"),
        (TranscriptCorruptError("damaged"), "MEETING_CORRUPT"),
    ],
)
def test_get_transcript_maps_a_failed_repair_at_the_boundary(
    rig: Rig, failure: MeetingScribeError, code: str
) -> None:
    rig.add(review=SpeakerReview(transcript_stale=True))
    rig.store.write_failure = failure
    rig.store.load_failure = failure if isinstance(failure, TranscriptCorruptError) else None

    assert rig.controller.transcript(MEETING_ID).failure().error_code == code  # type: ignore[union-attr]


def test_repair_logs_the_mapped_code_and_never_a_name(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.add(review=SpeakerReview(transcript_stale=True))
    rig.store.write_failure = TranscriptStorageError("io")

    with caplog.at_level(logging.WARNING):
        rig.controller.repair_stale().unwrap()

    assert "MEETING_STORAGE_FAILED" in caplog.text
    assert "TRANSCRIPT_STORAGE_FAILED" not in caplog.text
