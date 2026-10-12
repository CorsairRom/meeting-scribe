"""One OpenAI-compatible chat adapter: Ollama locally, the cloud gateway on request (spec 5).

It streams, so a cancel closes the HTTP stream between deltas and the read timeout means "no data
for N seconds". The ``openai`` client retries connection errors, 408, 409, 429 and 5xx with
backoff; a failure in the middle of a stream is not retried by the client, so one retry of the
whole step happens here. Failures map to stable summary errors whose messages carry at most an
HTTP status: never a request or response body, a transcript line or the key. The cloud key is
read from the ``CloudKeyStore`` for each request and kept only for it.
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx2
import openai
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryAuthFailedError,
    SummaryCancelledError,
    SummaryCloudKeyMissingError,
    SummaryContextTooLargeError,
    SummaryError,
    SummaryModelNotFoundError,
    SummaryOutputLimitReachedError,
    SummaryProviderRejectedError,
    SummaryProviderUnavailableError,
    SummaryRateLimitedError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.ports import (
    CancelSignal,
    CloudKeyStore,
    Completion,
    Message,
)
from meeting_scribe.summaries.domain.request import CompletionLimits
from meeting_scribe.summaries.domain.session import new_session_id

LOCAL_PLACEHOLDER_KEY = "ollama"
"""Ollama requires an API key and ignores it."""
LOCAL_REASONING_EFFORT = "none"
"""Turns the hidden thinking of local models off (verified on qwen3.5:9b, Ollama 0.18)."""
TRUNCATION_MARGIN_TOKENS = 16
CONNECT_TIMEOUT_SECONDS = 5.0
_POLL_SECONDS = 0.05
_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)
_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN, _HTTP_NOT_FOUND = 401, 403, 404
_HTTP_RATE_LIMITED, _HTTP_SERVER_ERROR = 429, 500
_HTTP_TRANSIENT = frozenset({408, 409})
_BACKOFF_START_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
_RETRY_AFTER_MAX_SECONDS = 60.0


@dataclass
class _Outcome:
    result: Result[Completion, SummaryError]
    kind: Literal["final", "transient", "midstream"] = "final"
    """``transient`` failed before the stream (retry with backoff); ``midstream`` broke after it."""
    delay: float | None = None
    """The provider's own ``Retry-After`` hint in seconds, when it sent one."""


def _retry_after(response: httpx2.Response) -> float | None:
    """The provider's ``retry-after-ms`` or ``retry-after`` (seconds) hint; ``None`` if absent.

    A hint that is not a number or lies outside ``0..60`` seconds is ignored, so the caller falls
    back to its own exponential backoff.
    """
    for header, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        try:
            seconds = float(response.headers[header]) / divisor
        except (KeyError, ValueError):
            continue
        if 0 <= seconds <= _RETRY_AFTER_MAX_SECONDS:
            return seconds
    return None


def _wire(messages: list[Message]) -> list[ChatCompletionMessageParam]:
    return cast(
        "list[ChatCompletionMessageParam]",
        [{"role": m.role, "content": m.content} for m in messages],
    )


class _Watchdog:
    """Closes the stream when the user cancels or the time is up (only real time wakes a stall)."""

    def __init__(self, cancel: CancelSignal, deadline: float | None) -> None:
        self._cancel = cancel
        self._deadline = deadline
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._stream: openai.Stream[ChatCompletionChunk] | None = None
        self.reason: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_Watchdog":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._done.set()
        self._thread.join()

    def attach(self, stream: "openai.Stream[ChatCompletionChunk]") -> None:
        with self._lock:
            self._stream = stream
            if self.reason is not None:
                stream.close()

    def _run(self) -> None:
        while not self._done.is_set():
            wait = _POLL_SECONDS
            if self._deadline is not None:
                wait = min(wait, max(self._deadline - time.monotonic(), 0.0))
            if self._cancel.wait(wait):
                self._fire("cancel")
                return
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._fire("deadline")
                return

    def _fire(self, reason: str) -> None:
        with self._lock:
            self.reason = reason
            if self._stream is not None:
                self._stream.close()


class OpenAiCompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        cloud: bool,
        max_retries: int,
        read_timeout_seconds: float,
        key_store: CloudKeyStore | None = None,
        reasoning_effort: str | None = None,
        user_agent: str | None = None,
        session_header: str | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._cloud = cloud
        self._max_retries = max_retries
        self._read_timeout = read_timeout_seconds
        self._key_store = key_store
        # Sent as ``reasoning_effort`` when set. Local thinking models (qwen3.5) otherwise spend
        # the whole output budget on hidden reasoning and answer with nothing.
        self._reasoning_effort = reasoning_effort
        # Gateways such as OpenCode Go reject anonymous requests (HTTP 400 MissingSessionID): they
        # want a client name and, per job, an opaque session id in ``session_header``.
        self._user_agent = user_agent
        self._session_header = session_header
        # Local transcripts must never go through HTTP_PROXY/ALL_PROXY, so the local client ignores
        # the proxy environment (trust_env=False). The cloud client keeps it: a user proxy to
        # reach the gateway may be legitimate, and the traffic is HTTPS anyway.
        self._http_client = http_client or openai.DefaultHttpx2Client(trust_env=cloud)

    def complete(
        self,
        messages: list[Message],
        limits: CompletionLimits,
        cancel: CancelSignal,
        timeout_seconds: float | None = None,
        session_id: str | None = None,
    ) -> Result[Completion, SummaryError]:
        if cancel.is_set():
            return Failure(SummaryCancelledError("The summary was cancelled."))
        key = self._key()
        if isinstance(key, Failure):
            return key
        # The client never retries (max_retries=0): the retries are ours so that a cancel or the
        # deadline is honoured between attempts and the pauses are interruptible.
        client = openai.OpenAI(
            base_url=self._base_url,
            api_key=key.unwrap(),
            max_retries=0,
            http_client=self._http_client,
            default_headers={"User-Agent": self._user_agent} if self._user_agent else None,
        )
        options = self._extra_options(session_id or new_session_id())
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        # ``max_retries`` is a budget per kind: up to that many transient retries, plus at most one
        # mid-stream retry (none when it is 0), so a request can make 1 + max_retries + 1 attempts.
        transient_retries = 0
        midstream_retried = False
        while True:
            outcome = self._attempt(client, messages, limits, cancel, deadline, options)
            if outcome.kind == "transient" and transient_retries < self._max_retries:
                delay = outcome.delay
                if delay is None:
                    delay = min(_BACKOFF_START_SECONDS * 2**transient_retries, _BACKOFF_MAX_SECONDS)
                transient_retries += 1
            elif outcome.kind == "midstream" and not midstream_retried and self._max_retries > 0:
                midstream_retried = True
                delay = 0.0
            else:
                return outcome.result
            stopped = self._pause(delay, cancel, deadline)
            if stopped is not None:
                return stopped.result

    @staticmethod
    def _pause(delay: float, cancel: CancelSignal, deadline: float | None) -> _Outcome | None:
        """Wait ``delay`` seconds, ending early on a cancel or when the time left runs out."""
        if deadline is not None:
            delay = min(delay, max(deadline - time.monotonic(), 0.0))
        if cancel.wait(delay) or cancel.is_set():
            return _Outcome(Failure(SummaryCancelledError("The summary was cancelled.")))
        if deadline is not None and time.monotonic() >= deadline:
            return _Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
        return None

    def _key(self) -> Result[str, SummaryError]:
        if not self._cloud:
            return Success(LOCAL_PLACEHOLDER_KEY)
        if self._key_store is None:
            return Failure(SummaryCloudKeyMissingError("No cloud key store is configured."))
        return self._key_store.get_key().map(lambda secret: secret.get_secret_value())

    def _attempt(
        self,
        client: openai.OpenAI,
        messages: list[Message],
        limits: CompletionLimits,
        cancel: CancelSignal,
        deadline: float | None,
        options: dict[str, Any],
    ) -> _Outcome:
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            return _Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
        read = self._read_timeout if left is None else min(self._read_timeout, left)
        connect = CONNECT_TIMEOUT_SECONDS if left is None else min(CONNECT_TIMEOUT_SECONDS, left)
        started = False
        with _Watchdog(cancel, deadline) as watchdog:
            try:
                stream = client.chat.completions.create(
                    model=limits.model,
                    messages=_wire(messages),
                    temperature=limits.temperature,
                    max_tokens=limits.max_output_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                    timeout=httpx2.Timeout(read, connect=connect),
                    **options,
                )
                started = True
                watchdog.attach(stream)
                completion = self._read(stream)
            except Exception as error:  # mapped below; nothing here may raise to the caller
                failure = self._map_error(error, limits)
                return self._stopped(cancel, watchdog) or self._failed(error, failure, started)
        stopped = self._stopped(cancel, watchdog)
        if stopped is not None:
            return stopped
        if completion is None:
            return _Outcome(
                Failure(SummaryProviderUnavailableError("The provider stream ended early.")),
                kind="midstream",
            )
        return _Outcome(self._guard(completion, limits))

    def _extra_options(self, session_id: str) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self._reasoning_effort is not None:
            options["reasoning_effort"] = self._reasoning_effort
        if self._session_header is not None:
            options["extra_headers"] = {self._session_header: session_id}
        return options

    @staticmethod
    def _failed(error: Exception, failure: SummaryError, started: bool) -> _Outcome:
        result: Result[Completion, SummaryError] = Failure(failure)
        if started:
            # A status error cannot happen after the first byte; anything else is a broken stream.
            return _Outcome(result, kind="midstream")
        if isinstance(error, openai.APIStatusError):
            status = error.status_code
            if (
                status in _HTTP_TRANSIENT
                or status == _HTTP_RATE_LIMITED
                or status >= _HTTP_SERVER_ERROR
            ):
                return _Outcome(result, kind="transient", delay=_retry_after(error.response))
            return _Outcome(result)
        return _Outcome(result, kind="transient")

    @staticmethod
    def _stopped(cancel: CancelSignal, watchdog: _Watchdog) -> _Outcome | None:
        if cancel.is_set():
            return _Outcome(Failure(SummaryCancelledError("The summary was cancelled.")))
        if watchdog.reason == "deadline":
            return _Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
        return None

    @staticmethod
    def _read(stream: "openai.Stream[ChatCompletionChunk]") -> Completion | None:
        parts: list[str] = []
        finish: str | None = None
        prompt_tokens = completion_tokens = None
        for event in stream:
            for choice in event.choices:
                if choice.delta.content:
                    parts.append(choice.delta.content)
                if choice.finish_reason:
                    finish = choice.finish_reason
            if event.usage is not None:
                prompt_tokens = event.usage.prompt_tokens
                completion_tokens = event.usage.completion_tokens
        if finish is None:
            return None
        text = _THINK.sub("", "".join(parts), count=1).strip()
        return Completion(
            text=text,
            finish_reason=finish,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _guard(
        self, completion: Completion, limits: CompletionLimits
    ) -> Result[Completion, SummaryError]:
        if completion.finish_reason == "length":
            return Failure(
                SummaryOutputLimitReachedError(
                    "The model used its whole output limit before finishing the summary, often "
                    "on hidden reasoning. Raise SUMMARIES_MAX_OUTPUT_TOKENS or choose another "
                    "model."
                )
            )
        reported = completion.prompt_tokens
        if (
            not self._cloud
            and reported is not None
            and reported >= limits.context_tokens - TRUNCATION_MARGIN_TOKENS
        ):
            return Failure(
                SummaryContextTooLargeError(
                    "The prompt reached the context window, so Ollama may have dropped the start "
                    "of it. Raise the Ollama context (OLLAMA_CONTEXT_LENGTH) and "
                    "SUMMARIES_LOCAL_CONTEXT_TOKENS together."
                )
            )
        return Success(completion)

    def _map_error(self, error: Exception, limits: CompletionLimits) -> SummaryError:
        if isinstance(error, openai.APITimeoutError | httpx2.TimeoutException):
            return SummaryTimeoutError(
                "The provider sent no data in time. Retry, or raise SUMMARIES_TIMEOUT_SECONDS."
            )
        if isinstance(error, openai.APIStatusError):
            return self._map_status(error.status_code, limits)
        return self._unavailable()

    def _map_status(self, status: int, limits: CompletionLimits) -> SummaryError:
        if status in {_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
            return SummaryAuthFailedError(
                "The provider rejected the key. Check the stored cloud API key."
            )
        if status == _HTTP_NOT_FOUND:
            if self._cloud:
                return SummaryModelNotFoundError(
                    "The cloud model was not found. Check SUMMARIES_CLOUD_MODEL."
                )
            return SummaryModelNotFoundError(
                f"The model is not available in Ollama. Run `ollama pull {limits.model}` "
                "or fix SUMMARIES_LOCAL_MODEL."
            )
        if status == _HTTP_RATE_LIMITED:
            return SummaryRateLimitedError("The provider is rate limiting. Try again later.")
        if status >= _HTTP_SERVER_ERROR or status in _HTTP_TRANSIENT:
            return self._unavailable()
        return SummaryProviderRejectedError(
            f"The provider rejected the request (HTTP {status}). Check the provider settings."
        )

    def _unavailable(self) -> SummaryProviderUnavailableError:
        if self._cloud:
            return SummaryProviderUnavailableError(
                "The cloud provider is unreachable. Check the network and try again."
            )
        return SummaryProviderUnavailableError(
            "Ollama is unreachable. Start Ollama (systemctl status ollama) and try again."
        )
