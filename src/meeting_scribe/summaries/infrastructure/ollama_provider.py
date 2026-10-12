"""The local summary adapter: Ollama's native ``/api/chat`` (spec 5).

The OpenAI-compatible endpoint of Ollama cannot set the context window per request (it stays at
4096), so long meetings split into many slow chunks. The native API takes ``options.num_ctx`` and
``options.num_predict`` per request, and ``think: false`` turns hidden reasoning off. The native
root is the origin of the configured loopback URL (``http://127.0.0.1:11434/v1`` -> ``/api/chat``).

It streams NDJSON, so a cancel closes the HTTP response between lines and the read timeout means
"no data for N seconds". Retries, the total deadline and the error mapping follow the
OpenAI-compatible adapter. Failures carry at most an HTTP status: never a request or response
body or a transcript line.

Ollama silently drops the BEGINNING of a prompt that exceeds ``num_ctx`` (the instructions) and
the model then ignores the format; the final chunk reports ``prompt_eval_count == num_ctx`` in
that case, which the guard turns into ``SUMMARY_CONTEXT_TOO_LARGE``.
"""

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx2
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryCancelledError,
    SummaryContextTooLargeError,
    SummaryError,
    SummaryModelNotFoundError,
    SummaryModelRunFailedError,
    SummaryOutputLimitReachedError,
    SummaryProviderRejectedError,
    SummaryProviderUnavailableError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.ports import CancelSignal, Completion, Message
from meeting_scribe.summaries.domain.request import CompletionLimits
from meeting_scribe.summaries.infrastructure.provider_support import (
    CONNECT_TIMEOUT_SECONDS,
    HTTP_SERVER_ERROR,
    Outcome,
    Watchdog,
    retry_after,
    run_with_retries,
    stopped_outcome,
)

TRUNCATION_MARGIN_TOKENS = 16
"""A prompt this close to ``num_ctx`` may already have lost its start."""
_HTTP_NOT_FOUND = 404
_HTTP_ERROR = 400
_HTTP_RETRIABLE = frozenset({408, 409, 429, 502, 503, 504})
"""Busy or in-between answers worth a retry. A plain 500 from a reachable Ollama means it could not
run the model (load failure, out of memory); repeating it only loads the GPU again."""


class _ModelFailureError(Exception):
    """The stream reported an error line: Ollama is up but the model run failed."""


class _BadStreamError(Exception):
    """A line of the stream was not a JSON object."""


class OllamaChatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        max_retries: int,
        read_timeout_seconds: float,
        http_client: httpx2.Client | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        self._url = f"{parts.scheme}://{parts.netloc}/api/chat"
        self._max_retries = max_retries
        self._read_timeout = read_timeout_seconds
        # Local transcripts must never go through HTTP_PROXY/ALL_PROXY: trust_env=False.
        self._http_client = http_client or httpx2.Client(trust_env=False)

    def complete(
        self,
        messages: list[Message],
        limits: CompletionLimits,
        cancel: CancelSignal,
        timeout_seconds: float | None = None,
        session_id: str | None = None,  # noqa: ARG002 (the cloud gateway needs it, Ollama not)
    ) -> Result[Completion, SummaryError]:
        if cancel.is_set():
            return Failure(SummaryCancelledError("The summary was cancelled."))
        body = {
            "model": limits.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "think": False,
            "options": {
                "num_ctx": limits.context_tokens,
                "num_predict": limits.max_output_tokens,
                "temperature": limits.temperature,
            },
        }
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        return run_with_retries(
            lambda: self._attempt(body, limits, cancel, deadline),
            max_retries=self._max_retries,
            cancel=cancel,
            deadline=deadline,
        )

    def _attempt(
        self,
        body: dict[str, Any],
        limits: CompletionLimits,
        cancel: CancelSignal,
        deadline: float | None,
    ) -> Outcome:
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            return Outcome(Failure(SummaryTimeoutError("The summary ran out of time.")))
        read = self._read_timeout if left is None else min(self._read_timeout, left)
        connect = CONNECT_TIMEOUT_SECONDS if left is None else min(CONNECT_TIMEOUT_SECONDS, left)
        started = False
        with Watchdog(cancel, deadline) as watchdog:
            try:
                with self._http_client.stream(
                    "POST", self._url, json=body, timeout=httpx2.Timeout(read, connect=connect)
                ) as response:
                    if response.status_code >= _HTTP_ERROR:
                        return self._status_outcome(response, limits)
                    started = True
                    watchdog.attach(response)
                    completion = self._read(response)
            except Exception as error:  # mapped below; nothing here may raise to the caller
                return stopped_outcome(cancel, watchdog) or self._error_outcome(error, started)
        return stopped_outcome(cancel, watchdog) or self._finished(completion, limits)

    def _error_outcome(self, error: Exception, started: bool) -> Outcome:
        if isinstance(error, _ModelFailureError):
            return Outcome(Failure(self._model_run_failed()))  # final: never retried
        return Outcome(
            Failure(self._map_error(error)), kind="midstream" if started else "transient"
        )

    def _finished(self, completion: Completion | None, limits: CompletionLimits) -> Outcome:
        if completion is None:
            return Outcome(
                Failure(SummaryProviderUnavailableError("The provider stream ended early.")),
                kind="midstream",
            )
        return Outcome(self._guard(completion, limits))

    @staticmethod
    def _read(response: httpx2.Response) -> Completion | None:
        parts: list[str] = []
        for line in response.iter_lines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as error:
                raise _BadStreamError from error
            if not isinstance(event, dict):
                raise _BadStreamError
            if "error" in event:
                raise _ModelFailureError
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            parts.append(content if isinstance(content, str) else "")
            if event.get("done"):
                return Completion(
                    text="".join(parts).strip(),
                    finish_reason=event.get("done_reason") or "stop",
                    prompt_tokens=event.get("prompt_eval_count"),
                    completion_tokens=event.get("eval_count"),
                )
        return None

    def _status_outcome(self, response: httpx2.Response, limits: CompletionLimits) -> Outcome:
        status = response.status_code
        failure = self._map_status(status, limits)
        if status in _HTTP_RETRIABLE:
            return Outcome(Failure(failure), kind="transient", delay=retry_after(response))
        return Outcome(Failure(failure))

    @staticmethod
    def _guard(
        completion: Completion, limits: CompletionLimits
    ) -> Result[Completion, SummaryError]:
        # The context check comes first: a prompt that filled the window also ends as "length".
        reported = completion.prompt_tokens
        if reported is not None and reported >= limits.context_tokens - TRUNCATION_MARGIN_TOKENS:
            return Failure(
                SummaryContextTooLargeError(
                    "The prompt reached the context window, so Ollama may have dropped the start "
                    "of it. Raise SUMMARIES_LOCAL_CONTEXT_TOKENS (the context the service asks "
                    "Ollama for) or choose a model that fits."
                )
            )
        if completion.finish_reason == "length":
            return Failure(
                SummaryOutputLimitReachedError(
                    "The model used its whole output limit before finishing. Raise "
                    "SUMMARIES_MAX_OUTPUT_TOKENS or SUMMARIES_NOTES_MAX_OUTPUT_TOKENS, or "
                    "choose another model."
                )
            )
        return Success(completion)

    def _map_error(self, error: Exception) -> SummaryError:
        if isinstance(error, httpx2.TimeoutException):
            return SummaryTimeoutError(
                "Ollama sent no data in time. Retry, or raise SUMMARIES_TIMEOUT_SECONDS."
            )
        if isinstance(error, _BadStreamError):
            return SummaryProviderUnavailableError(
                "Ollama sent an answer the service could not read. Try again."
            )
        return self._unavailable()

    def _map_status(self, status: int, limits: CompletionLimits) -> SummaryError:
        if status == _HTTP_NOT_FOUND:
            return SummaryModelNotFoundError(
                f"The model is not available in Ollama. Run `ollama pull {limits.model}` "
                "or fix SUMMARIES_LOCAL_MODEL."
            )
        if status in _HTTP_RETRIABLE:
            return self._unavailable()
        if status >= HTTP_SERVER_ERROR:
            return self._model_run_failed()
        return SummaryProviderRejectedError(
            f"Ollama rejected the request (HTTP {status}). Check the provider settings."
        )

    @staticmethod
    def _model_run_failed() -> SummaryModelRunFailedError:
        return SummaryModelRunFailedError(
            "Ollama could not run the model; check memory or pick a smaller model."
        )

    @staticmethod
    def _unavailable() -> SummaryProviderUnavailableError:
        return SummaryProviderUnavailableError(
            "Ollama is unreachable. Start Ollama (systemctl status ollama) and try again."
        )
