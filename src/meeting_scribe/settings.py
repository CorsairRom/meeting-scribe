"""Application settings: a root ``Settings`` composed of per-domain settings classes."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import (
    APP_DIR_NAME,
    MeetingScribeSettings,
    effective_env_files,
    xdg_env_file,
)
from meeting_scribe.recording.infrastructure.recording_settings import RecordingSettings

__all__ = [
    "APP_DIR_NAME",
    "Settings",
    "StorageSettings",
    "effective_env_files",
    "get_settings",
    "xdg_env_file",
]

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


# App-level settings live here. Domain settings (e.g. ``TranscriptionSettings``) are
# defined in each domain's ``infrastructure/`` and composed into ``Settings`` below.
class StorageSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="STORAGE_")

    meetings_dir: Path = Path.home() / "Documents" / "meetings"

    @field_validator("meetings_dir")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser()


class Settings(MeetingScribeSettings):
    log_level: LogLevel = "INFO"
    storage: StorageSettings = Field(default_factory=StorageSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
