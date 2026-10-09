"""Transcription parameters and the device policy (spec 6.4, AC-15)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import GpuUnavailableError

type Device = Literal["cuda", "cpu"]

AUTO_LANGUAGE = "auto"


class TranscriptionParams(BaseModel):
    """What the user can tune; the settings class is the single source of the values.

    Words always carry timestamps (``word_timestamps`` is part of the cache key, not a knob).
    """

    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    language: str
    compute_type: str = Field(min_length=1)
    cpu_compute_type: str = Field(min_length=1)
    beam_size: int = Field(ge=1)
    vad_filter: bool
    condition_on_previous_text: bool

    @field_validator("language")
    @classmethod
    def _language_is_a_code(cls, value: str) -> str:
        code = value.strip().lower()
        if not code:
            raise ValueError("language needs an ISO code or 'auto'")
        return code

    def effective_compute_type(self, device: Device) -> str:
        return self.compute_type if device == "cuda" else self.cpu_compute_type

    def device_label(self, device: Device) -> str:
        """What is recorded in ``processing.device``, for example ``cuda/float16``."""
        return f"{device}/{self.effective_compute_type(device)}"

    def whisper_language(self) -> str | None:
        """``None`` asks Whisper to detect the language from the first 30 seconds."""
        return None if self.language == AUTO_LANGUAGE else self.language


def check_device_available(
    device: Device, cuda_device_count: int
) -> Result[Device, GpuUnavailableError]:
    """``cuda`` without a CUDA device fails; nothing here (or after) falls back to the CPU."""
    if device == "cuda" and cuda_device_count < 1:
        return Failure(
            GpuUnavailableError(
                "No usable CUDA device was found. Fix the NVIDIA driver, or set "
                "PROCESSING_DEVICE=cpu to run on the CPU explicitly (slow)."
            )
        )
    return Success(device)
