"""Base entity with null-object support."""

from typing import Self

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success


class BaseEntity(BaseModel):
    """Immutable entity. State transitions return copies via ``model_copy(update=...)``.

    Repositories return a null entity (class name starting with ``_Null``) instead of
    ``None``; ``as_result`` converts it into a ``Failure``.
    """

    model_config = ConfigDict(frozen=True)

    id: str

    @property
    def is_null(self) -> bool:
        return type(self).__name__.startswith("_Null")

    def as_result[E: Exception](self, error: E) -> Result[Self, E]:
        if self.is_null:
            return Failure(error)
        return Success(self)
