"""Fakes for the summaries ports: no network, no model, nothing outside memory."""

from collections.abc import Callable

from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import SummaryError
from meeting_scribe.summaries.domain.ports import CancelSignal, Completion, Message
from meeting_scribe.summaries.domain.request import CompletionLimits

VALID_OUTPUT = """# Synthetic title
## Topics
### 1. Topic
- point
## Decisions
- decision
## Action items
| Owner | Task |
|---|---|
| Person A | task |
## Open questions
- question
"""

Responder = Callable[[list[Message]], str | Completion | SummaryError]


def by_prompt_kind(
    notes: str = "- note", merged: str = "- merged", final: str = VALID_OUTPUT
) -> Responder:
    """Map notes, merged notes, and ``final`` for the final and repair requests."""

    def respond(messages: list[Message]) -> str:
        system = messages[0].content
        if system.startswith("You extract compact notes"):
            return notes
        if system.startswith("You merge partial"):
            return merged
        return final

    return respond


class FakeProvider:
    """Records every request; answers from ``responder`` (never raises)."""

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or by_prompt_kind()
        self.requests: list[list[Message]] = []
        self.limits: list[CompletionLimits] = []
        self.on_call: Callable[[int], None] | None = None

    def complete(
        self,
        messages: list[Message],
        limits: CompletionLimits,
        cancel: CancelSignal,  # noqa: ARG002
    ) -> Result[Completion, SummaryError]:
        self.requests.append(messages)
        self.limits.append(limits)
        if self.on_call is not None:
            self.on_call(len(self.requests))
        answer = self.responder(messages)
        if isinstance(answer, SummaryError):
            return Failure(answer)
        if isinstance(answer, str):
            return Success(Completion(text=answer, finish_reason="stop"))
        return Success(answer)


class FakeReleaser:
    def __init__(self) -> None:
        self.calls = 0

    def release(self) -> None:
        self.calls += 1


class FakeCancel:
    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout: float | None = None) -> bool:  # noqa: ARG002
        return self._set


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now
