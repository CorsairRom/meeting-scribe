"""Value objects of the model setup: what is cached, the status a client sees, the failure rule."""

from dataclasses import dataclass
from typing import Literal

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadFailedError,
    ModelsDownloadTimeoutError,
    ModelsTokenKeyringLockedError,
)

type DownloadState = Literal["READY", "PENDING", "DOWNLOADING", "FAILED"]
type Outcome = Literal["READY", "FAILED", "CANCELLED"]

TOTAL_STEPS = 2
"""The pyannote pipeline, then Whisper (OQ-1: step-level progress)."""


@dataclass(frozen=True)
class CacheStatus:
    """Which models are completely in the Hugging Face cache (a partial download never counts)."""

    whisper_cached: bool
    pipeline_cached: bool

    @property
    def ready(self) -> bool:
        return self.whisper_cached and self.pipeline_cached


NOT_CACHED = CacheStatus(whisper_cached=False, pipeline_cached=False)


@dataclass(frozen=True)
class ModelsStatus:
    """What ``GetStatus`` reports. ``token_stored`` says the keyring item exists, not its value."""

    state: DownloadState
    whisper_cached: bool
    pipeline_cached: bool
    token_stored: bool
    step: int = 0
    total_steps: int = 0
    failure_code: str = ""


class FailurePolicy:
    """Which code a failed download reports (spec install.md section 8).

    The access and lookup codes the download process reports are the ones the user can act on, so
    they pass through; the timeout and the locked keyring keep their own; anything else is the
    generic ``MODELS_DOWNLOAD_FAILED``.
    """

    PASSTHROUGH = frozenset(
        {
            "DIARIZATION_MODEL_ACCESS_DENIED",
            "MODEL_UNAVAILABLE",
            ModelsDownloadTimeoutError.error_code,
            ModelsTokenKeyringLockedError.error_code,
        }
    )

    def code_for(self, error: MeetingScribeError) -> str:
        if error.error_code in self.PASSTHROUGH:
            return error.error_code
        return ModelsDownloadFailedError.error_code
