"""Model setup settings (``MODEL_SETUP_*``), composed into the root ``Settings``."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings


class ModelSetupSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="MODEL_SETUP_")

    download_timeout_seconds: float = Field(default=3600, gt=0)
    """How long the download child may run before it is stopped (about 1.65 GiB in total)."""
