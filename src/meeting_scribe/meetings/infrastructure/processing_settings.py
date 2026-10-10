"""Processing settings (``PROCESSING_*``), composed into the root ``Settings``."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings
from meeting_scribe.meetings.domain.retry_delays import check_retry_delays


class ProcessingSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="PROCESSING_")

    device: Literal["cuda", "cpu"] = "cuda"
    worker_timeout_factor: float = Field(default=1.0, gt=0)
    worker_min_timeout_seconds: float = Field(default=900, gt=0)
    worker_kill_grace_seconds: float = Field(default=5, gt=0)
    notify: bool = True
    storage_retry_delays_seconds: tuple[float, ...] = (2.0, 8.0, 30.0)

    @field_validator("storage_retry_delays_seconds")
    @classmethod
    def _delays_are_valid(cls, delays: tuple[float, ...]) -> tuple[float, ...]:
        return check_retry_delays(delays)
