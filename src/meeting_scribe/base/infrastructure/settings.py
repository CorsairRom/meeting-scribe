"""Shared settings base class and env-file resolution (used by every per-domain settings class)."""

import os
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

APP_DIR_NAME = "meeting-scribe"


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
