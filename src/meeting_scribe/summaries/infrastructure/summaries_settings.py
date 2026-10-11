"""Summaries settings (``SUMMARIES_*``), composed into the root ``Settings`` (spec 8).

The cloud API key is deliberately not a setting: it lives in the GNOME Keyring (ADR-009, spec 8a).
"""

import re
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings
from meeting_scribe.summaries.domain.request import MARGIN_TOKENS, CompletionLimits

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_LANGUAGE = re.compile(r"auto|[a-z]{2,3}")


class SummariesSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="SUMMARIES_")

    provider: Literal["local", "cloud"] = "local"
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = Field(default="qwen3.5:9b", min_length=1)
    local_context_tokens: int = Field(default=4096, gt=0)
    unload_local_model: bool = True
    cloud_enabled: bool = False
    cloud_provider_name: str = Field(default="OpenCode Go", min_length=1)
    cloud_base_url: str = "https://opencode.ai/zen/go/v1"
    cloud_model: str = Field(default="deepseek-v4-pro", min_length=1)
    cloud_context_tokens: int = Field(default=100000, gt=0)
    language: str = "auto"
    max_output_tokens: int = Field(default=2048, ge=256)
    temperature: float = Field(default=0.2, ge=0, le=1)
    timeout_seconds: float = Field(default=300, gt=0)
    total_timeout_seconds: float = Field(default=3600, gt=0)
    max_retries: int = Field(default=2, ge=0)

    @field_validator("local_base_url")
    @classmethod
    def _local_stays_on_loopback(cls, value: str) -> str:
        scheme, host = "", None
        try:
            parsed = urlparse(value)
            scheme, host = parsed.scheme, parsed.hostname
        except ValueError:
            pass
        if scheme in {"http", "https"} and host in _LOOPBACK_HOSTS and "@" not in value:
            return value
        raise ValueError(
            "SUMMARIES_LOCAL_BASE_URL must be an http URL on 127.0.0.1, ::1 or localhost"
        )

    @field_validator("cloud_base_url")
    @classmethod
    def _cloud_uses_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("SUMMARIES_CLOUD_BASE_URL must be an https URL")
        return value

    @field_validator("language")
    @classmethod
    def _language_is_auto_or_iso(cls, value: str) -> str:
        if _LANGUAGE.fullmatch(value) is None:
            raise ValueError("SUMMARIES_LANGUAGE must be 'auto' or a lowercase ISO code")
        return value

    @model_validator(mode="after")
    def _contexts_hold_the_output(self) -> Self:
        needed = self.max_output_tokens + MARGIN_TOKENS
        if self.local_context_tokens <= needed:
            raise ValueError(
                "SUMMARIES_LOCAL_CONTEXT_TOKENS must exceed SUMMARIES_MAX_OUTPUT_TOKENS + "
                f"{MARGIN_TOKENS}"
            )
        if self.cloud_context_tokens <= needed:
            raise ValueError(
                "SUMMARIES_CLOUD_CONTEXT_TOKENS must exceed SUMMARIES_MAX_OUTPUT_TOKENS + "
                f"{MARGIN_TOKENS}"
            )
        return self

    @property
    def local_label(self) -> str:
        return f"Local ({self.local_model})"

    @property
    def cloud_label(self) -> str:
        """The service-provided label every cloud entry point shows (safety requirement 3)."""
        return f"cloud: {self.cloud_provider_name}, {self.cloud_model}"

    def limits_for(self, *, cloud: bool) -> CompletionLimits:
        return CompletionLimits(
            model=self.cloud_model if cloud else self.local_model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            context_tokens=self.cloud_context_tokens if cloud else self.local_context_tokens,
        )
