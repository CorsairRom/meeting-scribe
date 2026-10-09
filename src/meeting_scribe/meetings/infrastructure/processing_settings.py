"""Processing settings (``PROCESSING_*``), composed into the root ``Settings``."""

from typing import Literal

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings


class ProcessingSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="PROCESSING_")

    device: Literal["cuda", "cpu"] = "cuda"
    worker_timeout_factor: float = Field(default=1.0, gt=0)
    worker_min_timeout_seconds: float = Field(default=900, gt=0)
    worker_kill_grace_seconds: float = Field(default=5, gt=0)
    notify: bool = True
