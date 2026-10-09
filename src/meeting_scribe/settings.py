"""Application settings: a root ``Settings`` composed of per-domain settings classes."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# App-level settings live here. Domain settings (e.g. ``TranscriptionSettings``) are
# defined in each domain's ``infrastructure/`` and composed into ``Settings`` below.
class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STORAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    meetings_dir: Path = Path.home() / "Documents" / "meetings"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = "INFO"
    storage: StorageSettings = Field(default_factory=StorageSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
