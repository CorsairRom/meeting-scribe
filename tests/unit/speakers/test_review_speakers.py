from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    InvalidValueError,
    MeetingScribeError,
)
from meeting_scribe.speakers.domain.errors import SpeakerNameTakenError
from meeting_scribe.speakers.domain.review import (
    MAX_NAME_LENGTH,
    ReviewState,
    SpeakerRecord,
    rename,
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
