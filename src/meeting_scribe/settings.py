"""Application settings: a root ``Settings`` composed of per-domain settings classes."""

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, TypeAdapter, ValidationError, field_validator
from pydantic_settings import SettingsConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.base.infrastructure.settings import (
    APP_DIR_NAME,
    MeetingScribeSettings,
    effective_env_files,
    file_layer,
    xdg_env_file,
    xdg_settings_file,
)
from meeting_scribe.meetings.infrastructure.processing_settings import ProcessingSettings
from meeting_scribe.preferences.domain.fields import (
    FIELD_REGISTRY,
    FieldCode,
    FieldProblem,
    FieldSource,
    FieldSpec,
    field_spec,
)
from meeting_scribe.recording.infrastructure.recording_settings import RecordingSettings
from meeting_scribe.speakers.infrastructure.speakers_settings import SpeakersSettings
from meeting_scribe.summaries.infrastructure.summaries_settings import SummariesSettings
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
    "xdg_settings_file",
]

logger = logging.getLogger(__name__)

LEGACY_CLOUD_ENABLED = "SUMMARIES_CLOUD_ENABLED"
LEGACY_SETTING = "LEGACY_SETTING"

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
    speakers: SpeakersSettings = Field(default_factory=SpeakersSettings)
    summaries: SummariesSettings = Field(default_factory=SummariesSettings)

    # Env files resolved when ``load`` ran; empty for settings built any other way.
    _env_files: tuple[Path, ...] = PrivateAttr(default=())
    # Which layer supplied each exposed field, computed once at load.
    _sources: dict[str, FieldSource] = PrivateAttr(default_factory=dict)

    @classmethod
    def load(cls, file_values: Mapping[str, str] | None = None, *, log_legacy: bool = True) -> Self:
        """Build settings from the environment and note the env file in effect.

        ``file_values`` are the user's saved values (``settings.json``): they fill only what the
        process environment and ``.env`` did not set (precedence: env > .env > file > defaults).

        Best effort, for the startup log only: the file is looked up here and again by each
        settings source (nested settings resolve their own), so a file created or removed in
        between would make ``env_files`` disagree with what was actually read.
        """
        saved = dict(file_values or {})
        env_files = effective_env_files()
        with file_layer(saved):
            settings = cls()
        settings._env_files = env_files
        settings._sources = _field_sources(saved, env_files)
        locked = _migrate_legacy_cloud_switch(settings, env_files, log=log_legacy)
        if locked is not None:
            settings._sources["summaries.provider"] = locked
        return settings

    @classmethod
    def with_overrides(cls, values: Mapping[str, str]) -> Result[Self, list[FieldProblem]]:
        """Load with ``values`` as the saved layer; invalid input is a list of per-field problems.

        The same validators that guard the environment run here (AC-3). Messages are the
        validators' own and never hold the offending value.
        """
        problems = [
            FieldProblem(key, FieldCode.UNKNOWN, "This setting cannot be changed here.")
            for key in values
            if field_spec(key) is None
        ]
        if problems:
            return Failure(problems)
        with file_layer(values):
            problems = _section_problems()
        if problems:
            return Failure(problems)
        try:
            return Success(cls.load(file_values=values, log_legacy=False))
        except ValidationError as error:
            return Failure(_problems_of(error, ""))

    def source_of(self, key: str) -> FieldSource:
        return self._sources.get(key, FieldSource())

    def exposed_value(self, key: str) -> str:
        """The effective value of an exposed field as text."""
        spec = _spec(key)
        return str(getattr(getattr(self, spec.section), spec.name))

    @staticmethod
    def exposed_default(key: str) -> str:
        spec = _spec(key)
        section = _section_class(spec.section)
        return str(section.model_fields[spec.name].default)

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


def _spec(key: str) -> FieldSpec:
    spec = field_spec(key)
    if spec is None:
        raise KeyError(key)
    return spec


def _section_class(section: str) -> type[MeetingScribeSettings]:
    annotation = Settings.model_fields[section].annotation
    if not (isinstance(annotation, type) and issubclass(annotation, MeetingScribeSettings)):
        raise KeyError(section)
    return annotation


def _env_name(spec: FieldSpec) -> str:
    prefix = _section_class(spec.section).model_config.get("env_prefix") or ""
    return f"{prefix}{spec.name}".upper()


def _field_sources(saved: Mapping[str, str], env_files: tuple[Path, ...]) -> dict[str, FieldSource]:
    """Which layer wins per exposed field: environment, ``.env``, saved file, else default."""
    environment = {name.upper() for name in os.environ}
    dotenv = {
        name.upper() for env_file in env_files for name in dotenv_values(env_file, encoding="utf-8")
    }
    sources: dict[str, FieldSource] = {}
    for spec in FIELD_REGISTRY:
        name = _env_name(spec)
        if name in environment:
            sources[spec.key] = FieldSource("env")
        elif name in dotenv:
            sources[spec.key] = FieldSource("dotenv")
        elif spec.key in saved:
            sources[spec.key] = FieldSource("file")
        else:
            sources[spec.key] = FieldSource("default")
    return sources


def _section_problems() -> list[FieldProblem]:
    """Build every exposed section alone (under the caller's file layer) and map its errors."""
    problems: list[FieldProblem] = []
    for section in dict.fromkeys(spec.section for spec in FIELD_REGISTRY):
        try:
            _section_class(section)()
        except ValidationError as error:
            problems.extend(_problems_of(error, section))
    return problems


def _problems_of(error: ValidationError, section: str) -> list[FieldProblem]:
    problems: list[FieldProblem] = []
    for detail in error.errors(include_input=False, include_url=False, include_context=False):
        location = str(detail["loc"][0]) if detail["loc"] else ""
        key = f"{section}.{location}" if section and location else (section or "settings")
        if field_spec(key) is None:
            key = section or "settings"  # an env-only field or a cross-field rule
        message = str(detail["msg"]).removeprefix("Value error, ")
        problems.append(FieldProblem(key, FieldCode.INVALID, message))
    return problems


def _legacy_cloud_switch(
    env_files: tuple[Path, ...],
) -> tuple[Literal["env", "dotenv"], bool] | None:
    """The obsolete ``SUMMARIES_CLOUD_ENABLED`` in the environment or ``.env`` (else None)."""
    raw: str | None = None
    layer: Literal["env", "dotenv"] = "env"
    for name, value in os.environ.items():
        if name.upper() == LEGACY_CLOUD_ENABLED:
            raw = value
    if raw is None:
        layer = "dotenv"
        for env_file in env_files:
            for name, value in dotenv_values(env_file, encoding="utf-8").items():
                if name.upper() == LEGACY_CLOUD_ENABLED and value is not None:
                    raw = value
    if raw is None:
        return None
    try:
        return layer, TypeAdapter(bool).validate_python(raw.strip().lower())
    except ValidationError:
        return None


def _migrate_legacy_cloud_switch(
    settings: Settings, env_files: tuple[Path, ...], *, log: bool
) -> FieldSource | None:
    """Nobody who deliberately kept the cloud off is switched on by the upgrade (spec section 7).

    Absent or true: ignored (true with one warning). Explicitly false while the provider is cloud:
    the provider resolves to local, locked with the ``LEGACY_SETTING`` reason. Values are never
    logged; nothing is rewritten. Returns the source that locks the provider, if any.
    """
    legacy = _legacy_cloud_switch(env_files)
    if legacy is None:
        return None
    layer, enabled = legacy
    if not enabled and settings.summaries.provider == "cloud":
        settings.summaries = settings.summaries.model_copy(update={"provider": "local"})
        if log:
            logger.warning(
                "%s is obsolete and keeps the cloud off: the provider stays local. "
                "Delete that line to choose the provider in Preferences.",
                LEGACY_CLOUD_ENABLED,
            )
        return FieldSource(layer, LEGACY_SETTING)
    if log:
        logger.warning(
            "%s is obsolete and ignored: the provider setting decides.", LEGACY_CLOUD_ENABLED
        )
    return None
