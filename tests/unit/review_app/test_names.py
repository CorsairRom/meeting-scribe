"""Client-side name checks mirror the service rules for instant feedback (AC-3, OQ-3)."""

from meeting_scribe_review.core.names import (
    MAX_NAME_LENGTH,
    NameProblem,
    NameProblemKind,
    check_name,
    problem_message,
)
from meeting_scribe_review.core.result import Err, Ok
from tests.unit.review_app.fakes import speaker

SPEAKERS = [
    speaker("ME", is_me=True),
    speaker("S1"),
    speaker("S2", name="Fake Alex"),
    speaker("S3", merged_into="S1", name="Fake Merged"),
]


def test_a_name_is_trimmed_and_normalised() -> None:
    assert check_name("  Fake Name  ", "S1", SPEAKERS) == Ok("Fake Name")
    assert check_name("Café", "S1", SPEAKERS) == Ok("Café")


def test_an_empty_name_clears_the_name() -> None:
    assert check_name("   ", "S1", SPEAKERS) == Ok("")


def test_a_name_of_64_characters_passes_and_65_fails() -> None:
    assert check_name("x" * MAX_NAME_LENGTH, "S1", SPEAKERS) == Ok("x" * MAX_NAME_LENGTH)
    assert check_name("x" * (MAX_NAME_LENGTH + 1), "S1", SPEAKERS) == Err(
        NameProblem(NameProblemKind.TOO_LONG)
    )


def test_length_counts_code_points_after_normalisation() -> None:
    assert isinstance(check_name("é" * MAX_NAME_LENGTH, "S1", SPEAKERS), Ok)


def test_control_and_format_characters_are_refused() -> None:
    expected = Err(NameProblem(NameProblemKind.CONTROL_CHARACTER))
    assert check_name("Fake\nName", "S1", SPEAKERS) == expected
    assert check_name("Fake\u200bName", "S1", SPEAKERS) == expected


def test_a_name_used_by_another_active_speaker_is_taken_ignoring_case() -> None:
    assert check_name("  fake ALEX ", "S1", SPEAKERS) == Err(
        NameProblem(NameProblemKind.TAKEN, speaker_id="S2")
    )


def test_a_default_label_of_another_active_speaker_is_taken() -> None:
    assert check_name("speaker 2", "S1", SPEAKERS) == Err(
        NameProblem(NameProblemKind.TAKEN, speaker_id="S2")
    )
    assert check_name("me", "S1", SPEAKERS) == Err(NameProblem(NameProblemKind.TAKEN, "ME"))


def test_a_speaker_may_keep_its_own_name_or_label() -> None:
    assert check_name("Fake Alex", "S2", SPEAKERS) == Ok("Fake Alex")
    assert check_name("Speaker 1", "S1", SPEAKERS) == Ok("Speaker 1")


def test_a_merged_away_speakers_name_does_not_clash() -> None:
    assert check_name("Fake Merged", "S1", SPEAKERS) == Ok("Fake Merged")


def test_every_problem_has_a_message_that_quotes_no_name() -> None:
    for kind in NameProblemKind:
        text = problem_message(NameProblem(kind, speaker_id="S2"))
        assert text and "S2" not in text and "Fake" not in text
