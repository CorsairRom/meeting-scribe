"""Shared settings base class and env-file resolution (used by every per-domain settings class)."""

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

APP_DIR_NAME = "meeting-scribe"


def xdg_env_file() -> Path:
    """``$XDG_CONFIG_HOME/meeting-scribe/.env`` (default ``~/.config/meeting-scribe/.env``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return config_home / APP_DIR_NAME / ".env"


def xdg_settings_file() -> Path:
    """``$XDG_CONFIG_HOME/meeting-scribe/settings.json``, next to the optional ``.env``."""
    return xdg_env_file().with_name("settings.json")


def effective_env_files() -> tuple[Path, ...]:
    """Env file in use: the XDG one if present, else ``./.env`` (dev). Empty if neither exists."""
    for candidate in (xdg_env_file(), Path.cwd() / ".env"):
        if candidate.is_file():
            return (candidate,)
    return ()


# The user's saved values ("<section>.<name>" -> text), read below the environment layers while a
# settings object is built under ``file_layer`` (precedence: env > .env > file > defaults).
_FILE_LAYER: ContextVar[Mapping[str, str] | None] = ContextVar("settings_file_layer", default=None)


@contextmanager
def file_layer(values: Mapping[str, str]) -> Iterator[None]:
    token = _FILE_LAYER.set(dict(values))
    try:
        yield
    finally:
        _FILE_LAYER.reset(token)


def _file_values_for(settings_cls: type[BaseSettings]) -> dict[str, str]:
    """The saved values of one settings class; its section is its env prefix, lowercased."""
    section = str(settings_cls.model_config.get("env_prefix") or "").rstrip("_").lower()
    if not section:
        return {}
    return {
        key.split(".", 1)[1]: value
        for key, value in (_FILE_LAYER.get() or {}).items()
        if key.split(".", 1)[0] == section
    }


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
        saved = InitSettingsSource(settings_cls, _file_values_for(settings_cls))
        return (init_settings, env_settings, dotenv, saved, file_secret_settings)
