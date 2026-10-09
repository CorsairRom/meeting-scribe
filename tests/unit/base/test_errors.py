import re

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    InvalidValueError,
    MeetingScribeError,
)


def _all_subclasses(cls: type[MeetingScribeError]) -> list[type[MeetingScribeError]]:
    found: list[type[MeetingScribeError]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_all_subclasses(sub))
    return found


def test_error_codes_are_stable() -> None:
    assert MeetingScribeError.error_code == "INTERNAL_ERROR"
    assert EntityNotFoundError.error_code == "ENTITY_NOT_FOUND"
    assert InvalidStateTransitionError.error_code == "INVALID_STATE_TRANSITION"
    assert InvalidValueError.error_code == "INVALID_VALUE"


def test_error_codes_are_upper_snake_and_unique() -> None:
    classes = [MeetingScribeError, *_all_subclasses(MeetingScribeError)]
    codes = [cls.error_code for cls in classes]

    assert all(re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", code) for code in codes)
    assert len(codes) == len(set(codes))


def test_detail_code_is_appended_to_instance_code_only() -> None:
    error = EntityNotFoundError("missing", detail_code="MEETING")

    assert error.error_code == "ENTITY_NOT_FOUND_MEETING"
    assert EntityNotFoundError.error_code == "ENTITY_NOT_FOUND"
    assert str(error) == "missing"


def test_instance_code_defaults_to_class_code() -> None:
    assert InvalidValueError("bad").error_code == "INVALID_VALUE"
