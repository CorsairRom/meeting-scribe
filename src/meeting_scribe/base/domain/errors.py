"""Domain error hierarchy with stable, machine-readable ``error_code`` values."""

import re

_DETAIL_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")


class MeetingScribeError(Exception):
    """Base class for all domain errors.

    ``error_code`` is a stable UPPER_SNAKE_CASE identifier. Passing ``detail_code``
    appends a suffix on the instance only (e.g. ``ENTITY_NOT_FOUND_MEETING``); it must match
    ``^[A-Z][A-Z0-9_]*$`` (an empty string is invalid) or ``ValueError`` is raised.
    """

    error_code: str = "INTERNAL_ERROR"

    def __init__(self, *args: object, detail_code: str | None = None) -> None:
        super().__init__(*args)
        base_code = type(self).error_code
        if detail_code is None:
            self.error_code = base_code
            return
        if not _DETAIL_CODE_PATTERN.fullmatch(detail_code):
            msg = f"detail_code must match ^[A-Z][A-Z0-9_]*$, got {detail_code!r}"
            raise ValueError(msg)
        self.error_code = f"{base_code}_{detail_code}"


class EntityNotFoundError(MeetingScribeError):
    error_code = "ENTITY_NOT_FOUND"


class InvalidStateTransitionError(MeetingScribeError):
    error_code = "INVALID_STATE_TRANSITION"


class InvalidValueError(MeetingScribeError):
    error_code = "INVALID_VALUE"


class GpuUnavailableError(MeetingScribeError):
    """No usable CUDA device or driver (shared by every GPU stage); never a silent CPU fallback."""

    error_code = "GPU_UNAVAILABLE"


class GpuOutOfMemoryError(MeetingScribeError):
    error_code = "GPU_OUT_OF_MEMORY"


class ModelUnavailableError(MeetingScribeError):
    """A model is neither cached nor downloadable (offline, renamed or unknown)."""

    error_code = "MODEL_UNAVAILABLE"
