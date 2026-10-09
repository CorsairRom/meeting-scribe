"""Application settings: a root ``Settings`` composed of per-domain settings classes."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

APP_DIR_NAME = "meeting-scribe"

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def xdg_env_file() -> Path:
    """``$XDG_CONFIG_HOME/meeting-scribe/.env`` (default ``~/.config/meeting-scribe/.env``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return config_home / APP_DIR_NAME / ".env"


def effective_env_files() -> tuple[Path, ...]:
    """Env file in use: the XDG one if present, else ``./.env`` (dev). Empty if neither exists."""
    for candidate in (xdg_env_file(), Path.cwd() / ".env"):
        if candidate.is_file():
            return (candidate,)
    return ()


class MeetingScribeSettings(BaseSettings):
    """Common configuration for every settings class; subclasses only set ``env_prefix``."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The env file is resolved at load time (XDG/CWD), not at class definition time.
        del dotenv_settings
        dotenv = DotEnvSettingsSource(settings_cls, env_file=effective_env_files() or None)
        return (init_settings, env_settings, dotenv, file_secret_settings)


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

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
