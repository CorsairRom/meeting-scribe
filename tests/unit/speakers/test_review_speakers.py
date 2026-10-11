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
    effective_utterances,
    merge,
    rename,
    unmerge,
    validate_name,
)
from meeting_scribe.speakers.domain.turns import Utterance


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

    rows = {row.id: row for row in effective_speakers(state).unwrap()}

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

    ids = [row.id for row in effective_speakers(state).unwrap()]

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
    rows = {row.id: row for row in effective_speakers(restored).unwrap()}
    assert rows["S1"].talk_seconds == pytest.approx(20.0)
    assert rows["S3"].merged_into == ""

    assert unmerge(restored, "S3").unwrap() == restored  # not merged: no-op success
    assert _failure(unmerge(restored, "S9")).error_code == "ENTITY_NOT_FOUND_SPEAKER"


def test_unmerge_after_the_target_was_merged_further_keeps_the_other_children() -> None:
    state = merge(_team(), "S2", "S1").unwrap()
    state = merge(state, "S1", "S3").unwrap()  # S2 now points at S3 as well

    restored = unmerge(state, "S2").unwrap()

    assert restored.merged_into == {"S1": "S3"}


def test_effective_utterances_show_merged_speakers_as_their_target() -> None:
    state = merge(_team(), "S3", "S1").unwrap()
    utterances = (
        Utterance(speaker="S3", start=1.0, end=2.0, text="first"),
        Utterance(speaker="S1", start=2.0, end=3.0, text="second"),
        Utterance(speaker="ME", start=3.0, end=4.0, text="third"),
    )

    shown = effective_utterances(utterances, state.merged_into)

    assert [u.speaker for u in shown] == ["S1", "S1", "ME"]
    assert [(u.start, u.end, u.text) for u in shown] == [
        (u.start, u.end, u.text) for u in utterances
    ]
    assert utterances[0].speaker == "S3"  # the stored utterances are never changed


def test_unmerge_reverts_the_name_the_target_adopted() -> None:
    merged = merge(_team(), "S3", "S1").unwrap()
    assert merged.find("S1").name == "Carol"  # type: ignore[union-attr]

    restored = unmerge(merged, "S3").unwrap()

    assert restored == _team()  # exactly the state before the merge, adoption record included


def test_unmerge_keeps_a_target_name_the_user_set_after_the_merge() -> None:
    merged = merge(_team(), "S3", "S1").unwrap()
    renamed = rename(merged, "S1", "Alex").unwrap()

    restored = unmerge(renamed, "S3").unwrap()

    assert restored.find("S1").name == "Alex"  # type: ignore[union-attr]
    assert restored.find("S3").name == "Carol"  # type: ignore[union-attr]


def test_renaming_the_target_to_the_adopted_name_counts_as_the_users_choice() -> None:
    merged = merge(_team(), "S3", "S1").unwrap()
    renamed = rename(merged, "S1", "Dave").unwrap()
    again = rename(renamed, "S1", "Carol").unwrap()

    # the name is the user's now, so it is kept and the source's identical name clashes with it
    assert type(_failure(unmerge(again, "S3"))) is SpeakerNameTakenError


def test_unmerge_does_not_revert_a_name_adopted_from_another_source() -> None:
    state = merge(_team(), "S3", "S1").unwrap()  # S1 adopts "Carol" from S3
    state = merge(state, "S2", "S1").unwrap()  # S1 already has a name: nothing adopted

    restored = unmerge(state, "S2").unwrap()

    assert restored.find("S1").name == "Carol"  # type: ignore[union-attr]


def test_unmerge_that_would_clash_with_an_active_name_is_rejected_and_changes_nothing() -> None:
    state = merge(_team(), "S3", "S1").unwrap()
    state = rename(state, "S1", "Alex").unwrap()
    state = rename(state, "S2", " carol ").unwrap()  # allowed: S3 is merged away

    failure = _failure(unmerge(state, "S3"))

    assert type(failure) is SpeakerNameTakenError
    assert "Carol" not in str(failure)
    assert state.merged_into == {"S3": "S1"}


def test_rename_of_a_merged_away_speaker_is_rejected() -> None:
    state = merge(_team(), "S3", "S1").unwrap()

    failure = _failure(rename(state, "S3", "Dave"))

    assert type(failure) is SpeakerMergeInvalidError
    assert state.find("S3").name == "Carol"  # type: ignore[union-attr]


def _chain() -> ReviewState:
    """S3 (Carol) merged into S1, then S1 merged into S2: both targets adopted "Carol"."""
    state = merge(_team(), "S3", "S1").unwrap()
    return merge(state, "S1", "S2").unwrap()


def _names(state: ReviewState) -> dict[str, str]:
    return {s.id: s.name for s in state.speakers}


def test_a_chained_merge_flattens_and_both_targets_adopt_the_name() -> None:
    chained = _chain()

    assert chained.merged_into == {"S3": "S2", "S1": "S2"}
    assert _names(chained)["S1"] == _names(chained)["S2"] == "Carol"


def test_chained_merges_unmerge_the_original_owner_first() -> None:
    restored = unmerge(_chain(), "S3").unwrap()

    assert restored.merged_into == {"S1": "S2"}
    assert _names(restored) == {"ME": "", "UNKNOWN": "", "S1": "", "S2": "", "S3": "Carol"}
    assert unmerge(restored, "S1").unwrap() == _team()


def test_chained_merges_unmerge_the_intermediate_speaker_first() -> None:
    restored = unmerge(_chain(), "S1").unwrap()

    assert restored.merged_into == {"S3": "S2"}
    assert _names(restored)["S2"] == ""  # S2 gives back what it took from S1
    assert _names(restored)["S1"] == "Carol"  # S1 still shows what it took from S3
    assert unmerge(restored, "S3").unwrap() == _team()


def test_a_user_rename_after_a_chained_merge_is_kept_on_unmerge() -> None:
    state = rename(_chain(), "S2", "Dave").unwrap()

    restored = unmerge(state, "S3").unwrap()

    assert _names(restored)["S2"] == "Dave"
    assert _names(restored)["S1"] == ""


_BAD_STATES = {
    "unknown source": {"S9": "S1"},
    "unknown target": {"S1": "S9"},
    "self merge": {"S1": "S1"},
    "cycle": {"S1": "S2", "S2": "S1"},
    "chain": {"S1": "S2", "S2": "S3"},
    "me as source": {"ME": "S1"},
}


@pytest.mark.parametrize("merged_into", list(_BAD_STATES.values()), ids=list(_BAD_STATES))
def test_an_inconsistent_state_is_a_typed_failure_never_an_exception(
    merged_into: dict[str, str],
) -> None:
    state = ReviewState(speakers=_team().speakers, merged_into=merged_into)

    for result in (
        effective_speakers(state),
        rename(state, "S1", "Dave"),
        merge(state, "S2", "S3"),
        unmerge(state, "S1"),
    ):
        assert _failure(result).error_code == "TRANSCRIPT_CORRUPT"


def test_a_state_with_unknown_adoption_ids_or_duplicate_speakers_is_corrupt() -> None:
    unknown = ReviewState(speakers=_team().speakers, merged_into={}, name_adopted_from={"S9": "S1"})
    duplicate = ReviewState(
        speakers=(*_team().speakers, _record("S1", "Speaker 1")), merged_into={}
    )

    assert _failure(effective_speakers(unknown)).error_code == "TRANSCRIPT_CORRUPT"
    assert _failure(effective_speakers(duplicate)).error_code == "TRANSCRIPT_CORRUPT"
