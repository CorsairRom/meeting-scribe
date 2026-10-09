from typing import ClassVar

import pytest
from pydantic import ValidationError
from returns.result import Failure, Success

from meeting_scribe.base.domain.entity import BaseEntity
from meeting_scribe.base.domain.errors import EntityNotFoundError


class Sample(BaseEntity):
    name: str


class _NullSample(Sample):
    _is_null_object: ClassVar[bool] = True
    id: str = ""
    name: str = ""


def test_entity_is_immutable() -> None:
    entity = Sample(id="1", name="a")

    with pytest.raises(ValidationError):
        entity.name = "b"  # type: ignore[misc]


def test_transition_returns_new_instance() -> None:
    entity = Sample(id="1", name="a")

    updated = entity.model_copy(update={"name": "b"})

    assert updated is not entity
    assert (entity.name, updated.name) == ("a", "b")
    assert updated.id == "1"


def test_real_entity_is_not_null_and_succeeds() -> None:
    entity = Sample(id="1", name="a")

    assert entity.is_null is False
    assert entity.as_result(EntityNotFoundError("x")) == Success(entity)


def test_null_entity_fails_with_given_error() -> None:
    null = _NullSample()
    error = EntityNotFoundError("sample 1")

    assert null.is_null is True
    assert null.as_result(error) == Failure(error)


def test_null_marker_is_independent_of_class_name() -> None:
    class Missing(Sample):
        _is_null_object: ClassVar[bool] = True
        id: str = ""
        name: str = ""

    assert Missing().is_null is True


def test_class_named_null_without_marker_is_not_null() -> None:
    class _NullLooking(Sample):
        pass

    assert _NullLooking(id="1", name="a").is_null is False


def test_subclass_of_null_object_stays_null() -> None:
    class MoreNull(_NullSample):
        pass

    assert MoreNull().is_null is True
