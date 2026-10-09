"""Recording settings (``RECORDING_*``), composed into the root ``Settings``."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings


class RecordingSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="RECORDING_")

    chunk_seconds: int = Field(default=30, ge=1)
    stop_timeout_seconds: float = Field(default=10, gt=0)
    ffmpeg_binary: str = "ffmpeg"
    sample_rate: int = Field(default=16000, ge=1)
