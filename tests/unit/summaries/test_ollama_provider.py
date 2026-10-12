"""The native Ollama chat adapter against an in-process fake transport: no socket, no real model."""

import json
import threading
import time
from collections.abc import Callable, Iterator

import httpx2
import pytest
from returns.result import Success

from meeting_scribe.summaries.domain.errors import (
    SummaryCancelledError,
    SummaryContextTooLargeError,
    SummaryModelNotFoundError,
    SummaryOutputLimitReachedError,
    SummaryProviderRejectedError,
    SummaryProviderUnavailableError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.ports import Message
from meeting_scribe.summaries.domain.request import CompletionLimits
from meeting_scribe.summaries.infrastructure.ollama_provider import OllamaChatProvider

BODY_MARKER = "PRIVATE-BODY-MARKER"
LOCAL_URL = "http://127.0.0.1:11434/v1"
LIMITS = CompletionLimits(
    model="model-x", temperature=0.2, max_output_tokens=512, context_tokens=4096
)
MESSAGES = [Message(role="system", content="rules"), Message(role="user", content="lines")]
Handler = Callable[[httpx2.Request], httpx2.Response]


def part(content: str = "", thinking: str | None = None) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if thinking is not None:
        message["thinking"] = thinking
    return {"model": "model-x", "message": message, "done": False}


def final(
    reason: str | None = "stop", prompt_tokens: int | None = 100, eval_tokens: int = 7
) -> dict[str, object]:
    chunk: dict[str, object] = {
        "model": "model-x",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "eval_count": eval_tokens,
    }
    if reason is not None:
        chunk["done_reason"] = reason
    if prompt_tokens is not None:
        chunk["prompt_eval_count"] = prompt_tokens
    return chunk


def ndjson(*events: dict[str, object]) -> bytes:
    return "".join(json.dumps(event) + "\n" for event in events).encode()


def ok_stream(text: str = "answer", prompt_tokens: int = 100) -> bytes:
    half = len(text) // 2
    return ndjson(part(text[:half]), part(text[half:]), final(prompt_tokens=prompt_tokens))


def ndjson_response(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "application/x-ndjson"}, content=body)


class Idle:
    def __init__(self) -> None:
        self._set = threading.Event()

    def is_set(self) -> bool:
        return self._set.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._set.wait(timeout)

    def set(self) -> None:
        self._set.set()


class Stalled(httpx2.SyncByteStream):
    """Sends one line, then blocks until closed (a model that went quiet)."""

    def __init__(self) -> None:
        self.released = threading.Event()

    def __iter__(self) -> Iterator[bytes]:
        yield ndjson(part("partial"))
        self.released.wait(10)

    def close(self) -> None:
        self.released.set()


class Broken(httpx2.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield ndjson(part("partial"))
        raise httpx2.ReadError("connection dropped")


def stalled_response(stream: httpx2.SyncByteStream) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "application/x-ndjson"}, stream=stream)


def make(
    handler: Handler,
    *,
    max_retries: int = 0,
    read_timeout: float = 30.0,
    requests: list[httpx2.Request] | None = None,
    base_url: str = LOCAL_URL,
) -> OllamaChatProvider:
    def record(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return handler(request)

    return OllamaChatProvider(
        base_url=base_url,
        max_retries=max_retries,
        read_timeout_seconds=read_timeout,
        http_client=httpx2.Client(transport=httpx2.MockTransport(record)),
    )


def complete(
    provider: OllamaChatProvider, cancel: Idle | None = None, timeout: float | None = None
):
    return provider.complete(MESSAGES, LIMITS, cancel or Idle(), timeout)


def test_the_request_is_a_native_chat_with_the_context_and_no_thinking() -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: ndjson_response(ok_stream()), requests=seen)

    complete(provider)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:11434/api/chat"
    body = json.loads(request.content)
    assert body["model"] == "model-x"
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "lines"},
    ]
    assert body["stream"] is True
    assert body["think"] is False
    assert body["options"] == {"num_ctx": 4096, "num_predict": 512, "temperature": 0.2}
    assert "authorization" not in request.headers


@pytest.mark.parametrize("base_url", ["http://localhost:11500/v1", "http://[::1]:11434"])
def test_the_native_root_is_the_origin_of_the_configured_url(base_url: str) -> None:
    seen: list[httpx2.Request] = []

    complete(make(lambda _r: ndjson_response(ok_stream()), requests=seen, base_url=base_url))

    assert seen[0].url.path == "/api/chat"
    assert seen[0].url.port == (11500 if "11500" in base_url else 11434)


def test_the_streamed_parts_are_assembled_with_reason_and_usage() -> None:
    completion = complete(make(lambda _r: ndjson_response(ok_stream("hello world", 321)))).unwrap()

    assert completion.text == "hello world"
    assert completion.finish_reason == "stop"
    assert completion.prompt_tokens == 321
    assert completion.completion_tokens == 7


def test_thinking_text_is_never_part_of_the_answer() -> None:
    body = ndjson(part("", thinking="private reasoning"), part("answer"), final())

    completion = complete(make(lambda _r: ndjson_response(body))).unwrap()

    assert completion.text == "answer"


def test_the_local_client_never_trusts_the_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")

    provider = OllamaChatProvider(base_url=LOCAL_URL, max_retries=0, read_timeout_seconds=1.0)

    assert provider._http_client._mounts == {}


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, SummaryModelNotFoundError),
        (400, SummaryProviderRejectedError),
        (401, SummaryProviderRejectedError),
        (500, SummaryProviderUnavailableError),
        (503, SummaryProviderUnavailableError),
    ],
)
def test_error_mapping_names_no_body(status: int, error: type[Exception]) -> None:
    failure = complete(make(lambda _r: httpx2.Response(status, text=BODY_MARKER))).failure()

    assert isinstance(failure, error)
    assert BODY_MARKER not in str(failure)


def test_a_missing_model_names_the_pull_command() -> None:
    failure = complete(make(lambda _r: httpx2.Response(404, text="x"))).failure()

    assert "ollama pull model-x" in str(failure)


def test_an_unreachable_ollama_is_unavailable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    failure = complete(make(refuse)).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)
    assert "Ollama" in str(failure)


def test_a_read_timeout_maps_to_timeout() -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    assert isinstance(complete(make(slow)).failure(), SummaryTimeoutError)


def test_a_server_error_is_retried_then_succeeds() -> None:
    seen: list[httpx2.Request] = []

    def handler(_r: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            return httpx2.Response(503, headers={"retry-after-ms": "1"}, text="x")
        return ndjson_response(ok_stream())

    assert isinstance(complete(make(handler, max_retries=1, requests=seen)), Success)
    assert len(seen) == 2


def test_a_rejected_request_is_not_retried() -> None:
    seen: list[httpx2.Request] = []

    complete(make(lambda _r: httpx2.Response(400, text="x"), max_retries=3, requests=seen))

    assert len(seen) == 1


def test_a_failure_in_the_middle_of_a_stream_is_retried_once() -> None:
    seen: list[httpx2.Request] = []

    def handler(_r: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            return stalled_response(Broken())
        return ndjson_response(ok_stream("second"))

    result = complete(make(handler, max_retries=2, requests=seen))

    assert result.unwrap().text == "second"
    assert len(seen) == 2


def test_an_error_line_in_the_stream_is_unavailable_and_not_quoted() -> None:
    body = ndjson(part("partial"), {"error": BODY_MARKER})

    failure = complete(make(lambda _r: ndjson_response(body))).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)
    assert BODY_MARKER not in str(failure)


def test_a_stream_that_never_finishes_is_a_failure_not_a_summary() -> None:
    failure = complete(make(lambda _r: ndjson_response(ndjson(part("partial"))))).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)


@pytest.mark.parametrize(("prompt_tokens", "fails"), [(4096, True), (4080, True), (4079, False)])
def test_truncation_guard(prompt_tokens: int, fails: bool) -> None:
    result = complete(make(lambda _r: ndjson_response(ok_stream(prompt_tokens=prompt_tokens))))

    if fails:
        failure = result.failure()
        assert isinstance(failure, SummaryContextTooLargeError)
        assert "SUMMARIES_LOCAL_CONTEXT_TOKENS" in str(failure)
    else:
        assert isinstance(result, Success)


def test_a_full_context_is_reported_before_the_cut_answer() -> None:
    body = ndjson(part("cut"), final("length", prompt_tokens=4096))

    failure = complete(make(lambda _r: ndjson_response(body))).failure()

    assert isinstance(failure, SummaryContextTooLargeError)


def test_the_guard_needs_the_reported_usage() -> None:
    body = ndjson(part("answer"), final(prompt_tokens=None))

    assert isinstance(complete(make(lambda _r: ndjson_response(body))), Success)


def test_an_answer_cut_at_the_output_limit_is_not_a_context_problem() -> None:
    body = ndjson(part("cut"), final("length", prompt_tokens=100))

    failure = complete(make(lambda _r: ndjson_response(body))).failure()

    assert isinstance(failure, SummaryOutputLimitReachedError)
    assert not isinstance(failure, SummaryContextTooLargeError)
    assert "SUMMARIES_MAX_OUTPUT_TOKENS" in str(failure)


def test_a_missing_done_reason_counts_as_stop() -> None:
    body = ndjson(part("answer"), final(None))

    assert complete(make(lambda _r: ndjson_response(body))).unwrap().finish_reason == "stop"


def test_a_cancel_before_the_call_makes_no_request() -> None:
    seen: list[httpx2.Request] = []
    cancel = Idle()
    cancel.set()

    failure = complete(make(lambda _r: ndjson_response(ok_stream()), requests=seen), cancel)

    assert isinstance(failure.failure(), SummaryCancelledError)
    assert seen == []


def test_a_cancel_aborts_a_stalled_stream_promptly() -> None:
    cancel = Idle()
    provider = make(lambda _r: stalled_response(Stalled()))
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    failure = complete(provider, cancel).failure()

    assert isinstance(failure, SummaryCancelledError)
    assert time.monotonic() - started < 3


def test_the_time_left_bounds_a_stalled_stream() -> None:
    provider = make(lambda _r: stalled_response(Stalled()))

    started = time.monotonic()
    failure = complete(provider, timeout=0.3).failure()

    assert isinstance(failure, SummaryTimeoutError)
    assert time.monotonic() - started < 3


def test_a_user_cancel_wins_over_an_expired_time() -> None:
    stalled = Stalled()

    class CancelOnceClosed(Idle):
        def is_set(self) -> bool:
            return stalled.released.is_set()

    provider = make(lambda _r: stalled_response(stalled))

    failure = complete(provider, CancelOnceClosed(), timeout=0.1).failure()

    assert isinstance(failure, SummaryCancelledError)


def test_the_request_timeout_is_the_smaller_of_the_idle_bound_and_the_time_left() -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: ndjson_response(ok_stream()), read_timeout=300, requests=seen)

    complete(provider, timeout=12.0)
    complete(provider)

    assert seen[0].extensions["timeout"]["read"] == pytest.approx(12.0, abs=0.5)
    assert seen[0].extensions["timeout"]["connect"] == 5.0
    assert seen[1].extensions["timeout"]["read"] == 300


def test_a_cancel_during_repeated_pre_stream_failures_returns_promptly() -> None:
    cancel = Idle()
    provider = make(lambda _r: httpx2.Response(503, text="x"), max_retries=6)
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    failure = complete(provider, cancel).failure()

    assert isinstance(failure, SummaryCancelledError)
    assert time.monotonic() - started < 2.0
