"""Base entity with null-object support."""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success


class BaseEntity(BaseModel):
    """Immutable entity. State transitions return copies via ``model_copy(update=...)``.

    Repositories return a null entity instead of ``None``: a subclass that sets the
    ``_is_null_object`` class marker to ``True`` (inherited by its own subclasses).
    ``as_result`` converts it into a ``Failure``.
    """

    model_config = ConfigDict(frozen=True)

    _is_null_object: ClassVar[bool] = False

    id: str

    @property
    def is_null(self) -> bool:
        return self._is_null_object

    def as_result[E: Exception](self, error: E) -> Result[Self, E]:
        if self.is_null:
            return Failure(error)
        return Success(self)
