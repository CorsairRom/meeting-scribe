"""Ports the summaries adapters implement (the provider, the model release) and the cancel shape."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
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

        ``timeout_seconds`` is the most this call may take (the time left of the total bound);
        ``None`` means no bound beyond the provider's own.
        """
        ...


class ModelReleaser(Protocol):
    def release(self) -> None:
        """Free the model from the GPU. Best effort: never raises; a no-op for the cloud."""
        ...
