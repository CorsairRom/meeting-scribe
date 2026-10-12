"""Summaries settings (``SUMMARIES_*``), composed into the root ``Settings`` (spec 8).

The cloud API key is deliberately not a setting: it lives in the GNOME Keyring (ADR-009, spec 8a).
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings
from meeting_scribe.summaries.domain.request import MARGIN_TOKENS, CompletionLimits

# The only hosts a local transcript may be sent to (AC-8); "localhost" is checked by name, not DNS.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_LANGUAGE = re.compile(r"auto|[a-z]{2,3}")
MAX_MODEL_NAME = 200
MIN_INPUT_TOKENS = 1024
"""Room a request must keep for its input: a larger output budget is clamped to leave this much."""
MIN_FINAL_OUTPUT_TOKENS = 256
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudProvider:
    name: str
    base_url: str
    session_header: str | None = None
    """Header that carries the opaque per-job session id, when the gateway requires one."""


# The cloud providers the user can choose in Preferences; the choice decides the display name and
# the base URL unless a developer override sets ``SUMMARIES_CLOUD_PROVIDER_NAME``/``_BASE_URL``.
CLOUD_PROVIDERS: dict[str, CloudProvider] = {
    "opencode-go": CloudProvider(
        "OpenCode Go", "https://opencode.ai/zen/go/v1", session_header="x-opencode-session"
    ),
}
DEFAULT_CLOUD_PROVIDER: Literal["opencode-go"] = "opencode-go"
_DEFAULT_CLOUD = CLOUD_PROVIDERS[DEFAULT_CLOUD_PROVIDER]


class SummariesSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="SUMMARIES_")

    provider: Literal["local", "cloud"] = "local"
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = Field(
        default="qwen3:4b-instruct-2507-q4_K_M", min_length=1, max_length=MAX_MODEL_NAME
    )
    local_context_tokens: int = Field(default=16384, gt=0)
    unload_local_model: bool = True
    cloud_provider: Literal["opencode-go"] = DEFAULT_CLOUD_PROVIDER
    cloud_provider_name: str = Field(default=_DEFAULT_CLOUD.name, min_length=1)
    cloud_base_url: str = _DEFAULT_CLOUD.base_url
    cloud_model: str = Field(default="deepseek-v4-pro", min_length=1, max_length=MAX_MODEL_NAME)
    cloud_context_tokens: int = Field(default=100000, gt=0)
    language: str = "auto"
    max_output_tokens: int = Field(default=3072, ge=256)
    notes_max_output_tokens: int = Field(default=768, ge=128)
    temperature: float = Field(default=0.2, ge=0, le=1)
    timeout_seconds: float = Field(default=300, gt=0)
    total_timeout_seconds: float = Field(default=3600, gt=0)
    max_retries: int = Field(default=2, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _cloud_provider_fills_its_defaults(cls, data: Any) -> Any:
        """The chosen cloud provider supplies the name and URL a developer override did not set."""
        if not isinstance(data, dict):
            return data
        provider = CLOUD_PROVIDERS.get(str(data.get("cloud_provider", DEFAULT_CLOUD_PROVIDER)))
        if provider is None:
            return data  # the Literal rejects the unknown choice with its own message
        return {"cloud_provider_name": provider.name, "cloud_base_url": provider.base_url, **data}

    @field_validator("local_base_url")
    @classmethod
    def _local_stays_on_loopback(cls, value: str) -> str:
        scheme, host = "", None
        try:
            parsed = urlparse(value)
            scheme, host = parsed.scheme, parsed.hostname
        except ValueError:
            pass
        # No "@": credentials in the URL are never needed for Ollama and obscure the real host.
        if scheme in {"http", "https"} and host in _LOOPBACK_HOSTS and "@" not in value:
            return value
        raise ValueError(
            "SUMMARIES_LOCAL_BASE_URL must be an http or https URL whose host is "
            "127.0.0.1, ::1 or localhost"
        )

    @field_validator("cloud_base_url")
    @classmethod
    def _cloud_uses_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("SUMMARIES_CLOUD_BASE_URL must be an https URL")
        return value

    @field_validator("local_model", "cloud_model")
    @classmethod
    def _model_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The model name must not be blank")
        return value.strip()

    @field_validator("language")
    @classmethod
    def _language_is_auto_or_iso(cls, value: str) -> str:
        if _LANGUAGE.fullmatch(value) is None:
            raise ValueError("SUMMARIES_LANGUAGE must be 'auto' or a lowercase ISO code")
        return value

    @model_validator(mode="after")
    def _contexts_leave_room_for_input(self) -> Self:
        """Reject only a context that cannot hold a useful request at all.

        A context smaller than the output budgets asks for is not an error: ``limits_for`` clamps
        the budgets, so a context saved before the 3072 default keeps loading.
        """
        needed = MIN_FINAL_OUTPUT_TOKENS + MARGIN_TOKENS + MIN_INPUT_TOKENS
        if self.local_context_tokens < needed:
            raise ValueError(f"SUMMARIES_LOCAL_CONTEXT_TOKENS must be at least {needed}")
        if self.cloud_context_tokens < needed:
            raise ValueError(f"SUMMARIES_CLOUD_CONTEXT_TOKENS must be at least {needed}")
        return self

    @property
    def local_label(self) -> str:
        return f"Local ({self.local_model})"

    @property
    def cloud_label(self) -> str:
        """The service-provided label every cloud entry point shows (safety requirement 3)."""
        return f"cloud: {self.cloud_provider_name}, {self.cloud_model}"

    @property
    def cloud_session_header(self) -> str | None:
        return CLOUD_PROVIDERS[self.cloud_provider].session_header

    def limits_for(self, *, cloud: bool) -> CompletionLimits:
        context = self.cloud_context_tokens if cloud else self.local_context_tokens
        cap = context - MARGIN_TOKENS - MIN_INPUT_TOKENS
        final, notes = min(self.max_output_tokens, cap), min(self.notes_max_output_tokens, cap)
        if (final, notes) != (self.max_output_tokens, self.notes_max_output_tokens):
            logger.warning(
                "The context of %d tokens is too small for SUMMARIES_MAX_OUTPUT_TOKENS=%d and "
                "SUMMARIES_NOTES_MAX_OUTPUT_TOKENS=%d; using %d and %d. Raise the context or "
                "lower the output limits.",
                context,
                self.max_output_tokens,
                self.notes_max_output_tokens,
                final,
                notes,
            )
        return CompletionLimits(
            model=self.cloud_model if cloud else self.local_model,
            temperature=self.temperature,
            max_output_tokens=final,
            context_tokens=context,
            notes_output_tokens=notes,
        )
