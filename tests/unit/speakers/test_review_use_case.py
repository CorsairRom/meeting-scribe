from returns.result import Failure

from meeting_scribe.speakers.application.review_speakers import ReviewSpeakers
from meeting_scribe.speakers.domain.review import ReviewState, SpeakerRecord


def _state() -> ReviewState:
    return ReviewState(
        speakers=(
            SpeakerRecord(id="S1", label="Speaker 1", name="Alex", talk_seconds=4.0),
            SpeakerRecord(id="S2", label="Speaker 2", talk_seconds=2.0),
        ),
        merged_into={},
    )


def test_a_change_is_reported_with_the_new_state() -> None:
    outcome = ReviewSpeakers().rename(_state(), "S2", "Bob").unwrap()

    assert outcome.changed is True
    assert outcome.state.find("S2").name == "Bob"  # type: ignore[union-attr]


def test_renaming_to_the_same_name_is_a_no_op() -> None:
    outcome = ReviewSpeakers().rename(_state(), "S1", "  Alex ").unwrap()

    assert outcome.changed is False
    assert outcome.state == _state()


def test_merge_and_unmerge_report_changes_and_unmerge_of_an_active_speaker_is_a_no_op() -> None:
    use_case = ReviewSpeakers()

    merged = use_case.merge(_state(), "S2", "S1").unwrap()
    assert merged.changed is True
    assert merged.state.merged_into == {"S2": "S1"}

    restored = use_case.unmerge(merged.state, "S2").unwrap()
    assert restored.changed is True

    again = use_case.unmerge(restored.state, "S2").unwrap()
    assert again.changed is False


def test_failures_pass_through() -> None:
    assert isinstance(ReviewSpeakers().merge(_state(), "S1", "S1"), Failure)
    assert isinstance(ReviewSpeakers().rename(_state(), "S2", "alex"), Failure)


def test_the_rows_are_the_effective_speakers() -> None:
    merged = ReviewSpeakers().merge(_state(), "S2", "S1").unwrap().state

    rows = ReviewSpeakers().speakers(merged)

    assert [(r.id, r.talk_seconds, r.merged_into) for r in rows] == [
        ("S1", 6.0, ""),
        ("S2", 2.0, "S1"),
    ]
