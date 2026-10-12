"""Ports the summaries adapters implement (the provider, the model release) and the cancel shape."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, SecretStr
from returns.result import Result

from meeting_scribe.summaries.domain.errors import SummaryError
from meeting_scribe.summaries.domain.request import CompletionLimits


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class Completion(BaseModel):
    """A finished answer: the text, why it stopped and the token usage the provider reported."""

    model_config = ConfigDict(frozen=True)

    text: str
    finish_reason: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


NOT_SUPPORTED_YET = "NOT_SUPPORTED_YET"
"""The ``reason`` of a listed model this version cannot use."""


class ModelEntry(BaseModel):
    """One model a provider offers; an unsupported one is listed but not selectable."""

    model_config = ConfigDict(frozen=True)

    id: str
    supported: bool = True
    reason: str = ""


class CancelSignal(Protocol):
    """Set from another thread to abort a summary (``threading.Event`` fits)."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class SummaryProvider(Protocol):
    def complete(
        self,
        messages: list[Message],
        limits: CompletionLimits,
        cancel: CancelSignal,
        timeout_seconds: float | None = None,
    ) -> Result[Completion, SummaryError]:
        """One chat completion. Never raises; aborts promptly when ``cancel`` is set.

        ``timeout_seconds`` is the most this call may take (the time left of the total bound); it
        is always positive (at least a small minimum), because the caller times out itself when
        nothing is left. ``None`` means no bound beyond the provider's own.
        """
        ...


class ModelReleaser(Protocol):
    def release(self) -> None:
        """Free the model from the GPU. Best effort: never raises; a no-op for the cloud."""
        ...


class NullModelReleaser:
    """Nothing to free: the cloud model is not on this GPU, or unloading is switched off."""

    def release(self) -> None:
        return None


class CloudKeyStore(Protocol):
    def get_key(self) -> Result[SecretStr, SummaryError]:
        """The cloud API key, read on demand and held by the caller for one request only.

        Never raises and never prompts: a missing key or an unreachable store is
        ``SUMMARY_CLOUD_KEY_MISSING``, a locked one ``SUMMARY_CLOUD_KEY_LOCKED``.
        """
        ...


class LocalModelSource(Protocol):
    def list_models(self) -> Result[list[ModelEntry], SummaryError]:
        """The models installed locally. Never raises; failure is ``SUMMARY_MODELS_UNAVAILABLE``."""
        ...


class CloudModelSource(Protocol):
    def list_models(self, key: SecretStr) -> Result[list[ModelEntry], SummaryError]:
        """The provider's models, asked with ``key`` for this call only. Never raises."""
        ...
