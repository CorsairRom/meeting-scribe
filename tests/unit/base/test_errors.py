import re

import pytest

from meeting_scribe.base.domain.errors import (
    EntityNotFoundError,
    GpuOutOfMemoryError,
    GpuUnavailableError,
    InvalidStateTransitionError,
    InvalidValueError,
    MeetingScribeError,
    ModelUnavailableError,
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


@pytest.mark.parametrize("code", ["MEETING", "A1", "NOT_FOUND_2", "X"])
def test_valid_detail_codes_are_accepted(code: str) -> None:
    assert EntityNotFoundError("x", detail_code=code).error_code == f"ENTITY_NOT_FOUND_{code}"


@pytest.mark.parametrize(
    "code", ["", "meeting", "Meeting", "1A", "_A", "A-B", "A B", "A\n", "ÁB", " "]
)
def test_invalid_detail_codes_raise_value_error(code: str) -> None:
    with pytest.raises(ValueError, match="detail_code"):
        EntityNotFoundError("x", detail_code=code)


def test_shared_gpu_and_model_error_codes_are_stable() -> None:
    assert GpuUnavailableError.error_code == "GPU_UNAVAILABLE"
    assert GpuOutOfMemoryError.error_code == "GPU_OUT_OF_MEMORY"
    assert ModelUnavailableError.error_code == "MODEL_UNAVAILABLE"
