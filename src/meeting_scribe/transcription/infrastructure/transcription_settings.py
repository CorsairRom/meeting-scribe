"""Transcription settings (``TRANSCRIPTION_*``), composed into the root ``Settings``."""

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings
from meeting_scribe.transcription.domain.params import TranscriptionParams


class TranscriptionSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="TRANSCRIPTION_")

    model: str = Field(default="large-v3-turbo", min_length=1)
    language: str = "es"
    compute_type: str = Field(default="float16", min_length=1)
    cpu_compute_type: str = Field(default="int8", min_length=1)
    beam_size: int = Field(default=5, ge=1)
    vad_filter: bool = True
    condition_on_previous_text: bool = False

    @field_validator("language")
    @classmethod
    def _language_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("TRANSCRIPTION_LANGUAGE needs an ISO code or 'auto'")
        return value

    def params(self) -> TranscriptionParams:
        """The snapshot the service puts in the job spec and the worker's cache key."""
        return TranscriptionParams.model_validate(self.model_dump())
