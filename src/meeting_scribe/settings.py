"""Application settings: a root ``Settings`` composed of per-domain settings classes."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import (
    APP_DIR_NAME,
    MeetingScribeSettings,
    effective_env_files,
    xdg_env_file,
)
from meeting_scribe.meetings.infrastructure.processing_settings import ProcessingSettings
from meeting_scribe.recording.infrastructure.recording_settings import RecordingSettings
from meeting_scribe.transcription.infrastructure.transcription_settings import (
    TranscriptionSettings,
)

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
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)

    # Env files resolved when ``load`` ran; empty for settings built any other way.
    _env_files: tuple[Path, ...] = PrivateAttr(default=())

    @classmethod
    def load(cls) -> Self:
        """Build settings from the environment and note the env file in effect.

        Best effort, for the startup log only: the file is looked up here and again by each
        settings source (nested settings resolve their own), so a file created or removed in
        between would make ``env_files`` disagree with what was actually read.
        """
        env_files = effective_env_files()
        settings = cls()
        settings._env_files = env_files
        return settings

    @property
    def env_files(self) -> tuple[Path, ...]:
        """Env files in use when these settings were loaded (empty if none or not loaded)."""
        return self._env_files

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings.load()
