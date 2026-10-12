"""The model list a client may pick from (spec ``preferences.md`` AC-16 to AC-19)."""

from dataclasses import dataclass

MODELS_OK = "OK"


@dataclass(frozen=True)
class ModelRow:
    id: str
    supported: bool
    reason: str  # empty for a supported model, else a code such as NOT_SUPPORTED_YET


@dataclass(frozen=True)
class ModelListing:
    """``status`` is ``OK`` or the code of why the list is unavailable; rows only when ``OK``."""

    status: str
    models: tuple[ModelRow, ...] = ()

    @property
    def available(self) -> bool:
        return self.status == MODELS_OK
