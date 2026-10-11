import pytest
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    InvalidValueError,
    MeetingScribeError,
)
from meeting_scribe.speakers.domain.errors import SpeakerMergeInvalidError, SpeakerNameTakenError
from meeting_scribe.speakers.domain.review import (
    MAX_NAME_LENGTH,
    ReviewState,
    SpeakerRecord,
    effective_speakers,
    merge,
    rename,
    unmerge,
    validate_name,
)


def _record(speaker_id: str, label: str, name: str = "", *, is_me: bool = False) -> SpeakerRecord:
    return SpeakerRecord(
        id=speaker_id,
        label=label,
        name=name,
        is_me=is_me,
        talk_seconds=10.0,
        utterance_count=2,
    )


def _state(*records: SpeakerRecord) -> ReviewState:
    return ReviewState(speakers=records, merged_into={})


def _failure(result: Result[object, MeetingScribeError]) -> MeetingScribeError:
    assert isinstance(result, Failure)
    return result.failure()


def test_name_rules() -> None:
    state = _state(_record("S1", "Speaker 1"), _record("S2", "Speaker 2", "Alex"))

    assert validate_name("  Bob  ") == Success("Bob")
    assert validate_name("   ") == Success("")
    assert validate_name("é") == Success("é")  # NFC
    assert validate_name("x" * MAX_NAME_LENGTH) == Success("x" * MAX_NAME_LENGTH)

    too_long = _failure(validate_name("x" * (MAX_NAME_LENGTH + 1)))
    assert type(too_long) is InvalidValueError
    for code in (0x0A, 0x09, 0x00, 0x200B, 0x202E):  # Cc: LF, TAB, NUL; Cf: ZWSP, RLO
        bad = "Al" + chr(code) + "ex"
        assert type(_failure(validate_name(bad))) is InvalidValueError

    taken = _failure(rename(state, "S1", " alex "))
    assert type(taken) is SpeakerNameTakenError
    assert taken.error_code == "SPEAKER_NAME_TAKEN"
    assert isinstance(taken, InvalidValueError)
    assert "alex" not in str(taken).lower()
    assert type(_failure(rename(state, "S1", "SPEAKER 2"))) is SpeakerNameTakenError

    renamed = rename(state, "S1", "  Bob ").unwrap()
    assert renamed.speakers[0].name == "Bob"
    cleared = rename(renamed, "S1", "  ").unwrap()
    assert cleared.speakers[0].name == ""
    assert rename(state, "S1", "Speaker 1").unwrap().speakers[0].name == "Speaker 1"
    assert (
        rename(state, "S2", "alex").unwrap().speakers[1].name == "alex"
    )  # own name is not a clash

    unknown = _failure(rename(state, "S9", "Bob"))
    assert unknown.error_code == "ENTITY_NOT_FOUND_SPEAKER"
    assert isinstance(unknown, EntityNotFoundError)
    assert state.speakers[0].name == ""  # the input state is never changed


def test_a_merged_speakers_label_and_name_do_not_block_a_name() -> None:
    merged = ReviewState(
        speakers=(_record("S1", "Speaker 1"), _record("S2", "Speaker 2", "Alex")),
        merged_into={"S2": "S1"},
    )

    assert rename(merged, "S1", "Alex").unwrap().speakers[0].name == "Alex"
    assert rename(merged, "S1", "Speaker 2").unwrap().speakers[0].name == "Speaker 2"


def _timed(
    speaker_id: str,
    label: str,
    talk: float,
    count: int,
    name: str = "",
    *,
    sample: str = "",
    is_me: bool = False,
) -> SpeakerRecord:
    return SpeakerRecord(
        id=speaker_id,
        label=label,
        name=name,
        is_me=is_me,
        talk_seconds=talk,
        utterance_count=count,
        sample=sample,
    )


def _team() -> ReviewState:
    return _state(
        _timed("ME", "Me", 5.0, 1, is_me=True),
        _timed("UNKNOWN", "Unknown", 1.0, 1),
        _timed("S1", "Speaker 1", 20.0, 4, sample="samples/S1.wav"),
        _timed("S2", "Speaker 2", 8.0, 3),
        _timed("S3", "Speaker 3", 2.5, 2, "Carol"),
    )


def test_effective_speakers_fold_merged_totals_into_the_target() -> None:
    state = rename(_team(), "S1", "Alex").unwrap()
    state = merge(state, "S2", "S1").unwrap()

    rows = {row.id: row for row in effective_speakers(state)}

    assert rows["S1"].display == "Alex"
    assert rows["S1"].talk_seconds == pytest.approx(28.0)
    assert rows["S1"].utterance_count == 7
    assert rows["S1"].has_sample is True
    assert rows["S1"].merged_into == ""
    assert rows["S2"].merged_into == "S1"
    assert rows["S2"].talk_seconds == pytest.approx(8.0)  # a merged row keeps its own totals
    assert rows["S2"].has_sample is False
    assert rows["ME"].is_me is True


def test_effective_speakers_list_me_first_then_by_talk_and_merged_last() -> None:
    state = merge(_team(), "S2", "S3").unwrap()

    ids = [row.id for row in effective_speakers(state)]

    assert ids == ["ME", "S1", "S3", "UNKNOWN", "S2"]


def test_merge_rules() -> None:
    state = _team()
    assert type(_failure(merge(state, "S1", "S1"))) is SpeakerMergeInvalidError
    assert _failure(merge(state, "S9", "S1")).error_code == "ENTITY_NOT_FOUND_SPEAKER"
    assert type(_failure(merge(state, "ME", "S1"))) is SpeakerMergeInvalidError
    assert _failure(merge(state, "S1", "S9")).error_code == "ENTITY_NOT_FOUND_SPEAKER"

    merged = merge(state, "S3", "S1").unwrap()
    assert type(_failure(merge(merged, "S3", "S2"))) is SpeakerMergeInvalidError  # source merged
    assert type(_failure(merge(merged, "S2", "S3"))) is SpeakerMergeInvalidError  # target merged

    # merge targets are never chained
    chained = merge(merged, "S1", "S2").unwrap()
    assert chained.merged_into == {"S3": "S2", "S1": "S2"}

    # "Me" can be a target; "Unknown" is a normal source
    into_me = merge(state, "UNKNOWN", "ME").unwrap()
    assert into_me.merged_into == {"UNKNOWN": "ME"}

    # the target takes the source's name only when it has none
    adopted = merge(state, "S3", "S1").unwrap()
    assert adopted.find("S1").name == "Carol"  # type: ignore[union-attr]
    assert adopted.find("S3").name == "Carol"  # type: ignore[union-attr]
    kept = merge(rename(state, "S1", "Alex").unwrap(), "S3", "S1").unwrap()
    assert kept.find("S1").name == "Alex"  # type: ignore[union-attr]
    assert merge(state, "S1", "S3").unwrap().find("S3").name == "Carol"  # type: ignore[union-attr]
    assert state.merged_into == {}


def test_unmerge_restores_the_speaker() -> None:
    state = merge(_team(), "S3", "S1").unwrap()

    restored = unmerge(state, "S3").unwrap()

    assert restored.merged_into == {}
    assert restored.find("S3") == _team().find("S3")  # its own name, totals and sample
    rows = {row.id: row for row in effective_speakers(restored)}
    assert rows["S1"].talk_seconds == pytest.approx(20.0)
    assert rows["S3"].merged_into == ""

    assert unmerge(restored, "S3").unwrap() == restored  # not merged: no-op success
    assert _failure(unmerge(restored, "S9")).error_code == "ENTITY_NOT_FOUND_SPEAKER"


def test_unmerge_after_the_target_was_merged_further_keeps_the_other_children() -> None:
    state = merge(_team(), "S3", "S1").unwrap()
    state = merge(state, "S1", "S2").unwrap()

    restored = unmerge(state, "S3").unwrap()

    assert restored.merged_into == {"S1": "S2"}
