"""Domain error hierarchy with stable, machine-readable ``error_code`` values."""


class MeetingScribeError(Exception):
    """Base class for all domain errors.

    ``error_code`` is a stable UPPER_SNAKE_CASE identifier. Passing ``detail_code``
    appends a suffix on the instance only (e.g. ``ENTITY_NOT_FOUND_MEETING``).
    """

    error_code: str = "INTERNAL_ERROR"

    def __init__(self, *args: object, detail_code: str | None = None) -> None:
        super().__init__(*args)
        base_code = type(self).error_code
        self.error_code = f"{base_code}_{detail_code}" if detail_code else base_code


class EntityNotFoundError(MeetingScribeError):
    error_code = "ENTITY_NOT_FOUND"


class InvalidStateTransitionError(MeetingScribeError):
    error_code = "INVALID_STATE_TRANSITION"


class InvalidValueError(MeetingScribeError):
    error_code = "INVALID_VALUE"
