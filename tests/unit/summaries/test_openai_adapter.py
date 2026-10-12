"""The OpenAI-compatible adapter against an in-process fake transport: no socket, no real key."""

import json
import threading
import time
from collections.abc import Callable, Iterator

import httpx2
import pytest
from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryAuthFailedError,
    SummaryCancelledError,
    SummaryCloudKeyLockedError,
    SummaryContextTooLargeError,
    SummaryError,
    SummaryOutputLimitReachedError,
    SummaryModelNotFoundError,
    SummaryProviderRejectedError,
    SummaryProviderUnavailableError,
    SummaryRateLimitedError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.ports import Message
from meeting_scribe.summaries.domain.request import CompletionLimits
from meeting_scribe.summaries.infrastructure.openai_provider import OpenAiCompatibleProvider

KEY = "test-key-not-a-secret"
BODY_MARKER = "PRIVATE-BODY-MARKER"
LOCAL_URL = "http://127.0.0.1:11434/v1"
CLOUD_URL = "https://gateway.invalid/v1"
LIMITS = CompletionLimits(
    model="model-x", temperature=0.2, max_output_tokens=512, context_tokens=4096
)
MESSAGES = [Message(role="system", content="rules"), Message(role="user", content="lines")]
Handler = Callable[[httpx2.Request], httpx2.Response]


def sse(*events: dict[str, object], done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def chunk(
    content: str | None = None, finish: str | None = None, usage: dict[str, int] | None = None
) -> dict[str, object]:
    choices = (
        []
        if usage and content is None and finish is None
        else [
            {
                "index": 0,
                "delta": {"content": content} if content is not None else {},
                "finish_reason": finish,
            }
        ]
    )
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "model": "model-x",
        "choices": choices,
        "usage": usage,
    }


def ok_stream(text: str = "answer", prompt_tokens: int = 100) -> bytes:
    half = len(text) // 2
    return sse(
        chunk(text[:half]),
        chunk(text[half:]),
        chunk(finish="stop"),
        chunk(usage={"prompt_tokens": prompt_tokens, "completion_tokens": 7, "total_tokens": 107}),
    )


def sse_response(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


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
    """Sends one event, then blocks until closed (a model that went quiet)."""

    def __init__(self) -> None:
        self.released = threading.Event()

    def __iter__(self) -> Iterator[bytes]:
        yield sse(chunk("partial"), done=False)
        self.released.wait(10)

    def close(self) -> None:
        self.released.set()


class Broken(httpx2.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield sse(chunk("partial"), done=False)
        raise httpx2.ReadError("connection dropped")


class FakeKeyStore:
    def __init__(self, result: Result[SecretStr, SummaryError] | None = None) -> None:
        self.calls = 0
        self._result = result or Success(SecretStr(KEY))

    def get_key(self) -> Result[SecretStr, SummaryError]:
        self.calls += 1
        return self._result


def make(
    handler: Handler,
    *,
    cloud: bool = False,
    max_retries: int = 0,
    read_timeout: float = 30.0,
    requests: list[httpx2.Request] | None = None,
    key_store: FakeKeyStore | None = None,
    reasoning_effort: str | None = None,
) -> OpenAiCompatibleProvider:
    def record(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return handler(request)

    return OpenAiCompatibleProvider(
        base_url=CLOUD_URL if cloud else LOCAL_URL,
        cloud=cloud,
        key_store=(key_store or FakeKeyStore()) if cloud else None,
        max_retries=max_retries,
        read_timeout_seconds=read_timeout,
        reasoning_effort=reasoning_effort,
        http_client=httpx2.Client(transport=httpx2.MockTransport(record)),
    )


def complete(
    provider: OpenAiCompatibleProvider, cancel: Idle | None = None, timeout: float | None = None
):
    return provider.complete(MESSAGES, LIMITS, cancel or Idle(), timeout)


def test_the_request_has_the_chat_completions_shape() -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: sse_response(ok_stream()), requests=seen)

    complete(provider)

    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "model-x"
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "lines"},
    ]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 512
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_reasoning_effort_is_sent_when_the_provider_has_it() -> None:
    seen: list[httpx2.Request] = []
    provider = make(
        lambda _r: sse_response(ok_stream()), requests=seen, reasoning_effort="none"
    )

    complete(provider)

    assert json.loads(seen[0].content)["reasoning_effort"] == "none"


@pytest.mark.parametrize("cloud", [False, True])
def test_reasoning_effort_is_not_sent_by_default(cloud: bool) -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: sse_response(ok_stream()), cloud=cloud, requests=seen)

    complete(provider)

    assert "reasoning_effort" not in json.loads(seen[0].content)


def test_the_streamed_deltas_are_assembled_with_finish_reason_and_usage() -> None:
    result = complete(make(lambda _r: sse_response(ok_stream("hello world", 321))))

    completion = result.unwrap()
    assert completion.text == "hello world"
    assert completion.finish_reason == "stop"
    assert completion.prompt_tokens == 321
    assert completion.completion_tokens == 7


def test_a_leading_think_block_is_stripped_and_never_kept() -> None:
    body = sse(chunk("<think>reasoning "), chunk("here</think>\n\nanswer"), chunk(finish="stop"))

    completion = complete(make(lambda _r: sse_response(body))).unwrap()

    assert completion.text == "answer"
    assert "reasoning" not in completion.text


def test_key_only_in_the_header() -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: sse_response(ok_stream()), cloud=True, requests=seen)

    complete(provider)

    request = seen[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    assert KEY.encode() not in request.content
    assert all(
        KEY not in value for name, value in request.headers.items() if name != "authorization"
    )


def test_a_missing_or_locked_key_fails_before_any_request() -> None:
    seen: list[httpx2.Request] = []
    locked = FakeKeyStore(Failure(SummaryCloudKeyLockedError("locked")))

    result = complete(
        make(lambda _r: sse_response(ok_stream()), cloud=True, requests=seen, key_store=locked)
    )

    assert isinstance(result.failure(), SummaryCloudKeyLockedError)
    assert seen == []


def test_the_key_is_read_for_each_cloud_request_and_never_for_the_local_one() -> None:
    store = FakeKeyStore()
    cloud = make(lambda _r: sse_response(ok_stream()), cloud=True, key_store=store)
    local = make(lambda _r: sse_response(ok_stream()))

    complete(cloud)
    complete(cloud)
    complete(local)

    assert store.calls == 2


def test_the_local_provider_sends_a_fixed_placeholder_key() -> None:
    seen: list[httpx2.Request] = []

    complete(make(lambda _r: sse_response(ok_stream()), requests=seen))

    assert seen[0].headers["authorization"] == "Bearer ollama"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, SummaryAuthFailedError),
        (403, SummaryAuthFailedError),
        (404, SummaryModelNotFoundError),
        (429, SummaryRateLimitedError),
        (400, SummaryProviderRejectedError),
        (402, SummaryProviderRejectedError),
        (422, SummaryProviderRejectedError),
        (500, SummaryProviderUnavailableError),
        (503, SummaryProviderUnavailableError),
    ],
)
def test_error_mapping(status: int, error: type[Exception]) -> None:
    provider = make(lambda _r: httpx2.Response(status, json={"error": {"message": BODY_MARKER}}))

    failure = complete(provider).failure()

    assert isinstance(failure, error)
    assert BODY_MARKER not in str(failure)


def test_a_rejected_request_names_only_the_http_status() -> None:
    failure = complete(make(lambda _r: httpx2.Response(402, text=BODY_MARKER))).failure()

    assert "402" in str(failure)
    assert BODY_MARKER not in str(failure)


def test_a_missing_local_model_names_the_pull_command() -> None:
    failure = complete(make(lambda _r: httpx2.Response(404, text="x"))).failure()

    assert "ollama pull model-x" in str(failure)


def test_an_unreachable_provider_is_unavailable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    failure = complete(make(refuse)).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)
    assert "Ollama" in str(failure)


def test_a_read_timeout_maps_to_timeout() -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    assert isinstance(complete(make(slow)).failure(), SummaryTimeoutError)


def test_a_rate_limit_is_retried_then_succeeds() -> None:
    seen: list[httpx2.Request] = []

    def handler(_r: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            return httpx2.Response(429, headers={"retry-after-ms": "1"}, text="x")
        return sse_response(ok_stream())

    result = complete(make(handler, max_retries=2, requests=seen))

    assert isinstance(result, Success)
    assert len(seen) == 2


def test_a_persistent_rate_limit_fails_after_the_configured_retries() -> None:
    seen: list[httpx2.Request] = []
    provider = make(
        lambda _r: httpx2.Response(429, headers={"retry-after-ms": "1"}, text="x"),
        max_retries=2,
        requests=seen,
    )

    failure = complete(provider).failure()

    assert isinstance(failure, SummaryRateLimitedError)
    assert len(seen) == 3


def test_a_server_error_is_retried() -> None:
    seen: list[httpx2.Request] = []

    def handler(_r: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            return httpx2.Response(503, headers={"retry-after-ms": "1"}, text="x")
        return sse_response(ok_stream())

    assert isinstance(complete(make(handler, max_retries=1, requests=seen)), Success)


def test_an_auth_failure_is_not_retried() -> None:
    seen: list[httpx2.Request] = []

    complete(make(lambda _r: httpx2.Response(401, text="x"), max_retries=3, requests=seen))

    assert len(seen) == 1


def test_a_failure_in_the_middle_of_a_stream_is_retried_once() -> None:
    seen: list[httpx2.Request] = []

    def handler(_r: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Broken()
            )
        return sse_response(ok_stream("second"))

    result = complete(make(handler, max_retries=2, requests=seen))

    assert result.unwrap().text == "second"
    assert len(seen) == 2


def test_a_failure_in_the_middle_of_a_stream_is_not_retried_without_retries() -> None:
    seen: list[httpx2.Request] = []
    provider = make(
        lambda _r: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Broken()
        ),
        max_retries=0,
        requests=seen,
    )

    failure = complete(provider).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)
    assert len(seen) == 1


def test_a_stream_that_never_finishes_is_a_failure_not_a_summary() -> None:
    body = sse(chunk("partial"), done=True)

    failure = complete(make(lambda _r: sse_response(body))).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)


@pytest.mark.parametrize(("prompt_tokens", "fails"), [(4096, True), (4080, True), (4079, False)])
def test_truncation_guard(prompt_tokens: int, fails: bool) -> None:
    result = complete(make(lambda _r: sse_response(ok_stream(prompt_tokens=prompt_tokens))))

    if fails:
        failure = result.failure()
        assert isinstance(failure, SummaryContextTooLargeError)
        assert "OLLAMA_CONTEXT_LENGTH" in str(failure)
        assert "SUMMARIES_LOCAL_CONTEXT_TOKENS" in str(failure)
    else:
        assert isinstance(result, Success)


def test_the_guard_needs_the_reported_usage() -> None:
    body = sse(chunk("answer"), chunk(finish="stop"))

    assert isinstance(complete(make(lambda _r: sse_response(body))), Success)


def test_the_cloud_budget_is_not_a_hard_window() -> None:
    result = complete(make(lambda _r: sse_response(ok_stream(prompt_tokens=4090)), cloud=True))

    assert isinstance(result, Success)


@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize("text", ["cut", None])
def test_an_answer_cut_at_the_output_limit_is_not_a_context_problem(
    cloud: bool, text: str | None
) -> None:
    body = sse(chunk(text), chunk(finish="length"))

    failure = complete(make(lambda _r: sse_response(body), cloud=cloud)).failure()

    assert isinstance(failure, SummaryOutputLimitReachedError)
    assert not isinstance(failure, SummaryContextTooLargeError)
    assert "SUMMARIES_MAX_OUTPUT_TOKENS" in str(failure)


def test_an_empty_answer_is_returned_for_the_use_case_to_judge() -> None:
    body = sse(chunk(finish="stop"))

    assert complete(make(lambda _r: sse_response(body))).unwrap().text == ""


def test_a_cancel_before_the_call_makes_no_request() -> None:
    seen: list[httpx2.Request] = []
    cancel = Idle()
    cancel.set()

    failure = complete(make(lambda _r: sse_response(ok_stream()), requests=seen), cancel).failure()

    assert isinstance(failure, SummaryCancelledError)
    assert seen == []


def test_a_cancel_aborts_a_stalled_stream_promptly() -> None:
    cancel = Idle()
    stalled = Stalled()
    provider = make(
        lambda _r: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stalled
        )
    )
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    failure = complete(provider, cancel).failure()

    assert isinstance(failure, SummaryCancelledError)
    assert time.monotonic() - started < 3


def test_the_time_left_bounds_a_stalled_stream() -> None:
    stalled = Stalled()
    provider = make(
        lambda _r: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stalled
        )
    )

    started = time.monotonic()
    failure = complete(provider, timeout=0.3).failure()

    assert isinstance(failure, SummaryTimeoutError)
    assert time.monotonic() - started < 3


def test_a_user_cancel_wins_over_an_expired_time() -> None:
    stalled = Stalled()

    class CancelOnceClosed(Idle):
        """Not set while the stream stalls; set once the deadline has closed it.

        The deadline fires first and, when the outcome is judged, both hold: the cancel must win.
        """

        def is_set(self) -> bool:
            return stalled.released.is_set()

    provider = make(
        lambda _r: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stalled
        )
    )

    failure = complete(provider, CancelOnceClosed(), timeout=0.1).failure()

    assert stalled.released.is_set()
    assert isinstance(failure, SummaryCancelledError)


def test_the_request_timeout_is_the_smaller_of_the_idle_bound_and_the_time_left() -> None:
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: sse_response(ok_stream()), read_timeout=300, requests=seen)

    complete(provider, timeout=12.0)
    complete(provider)

    assert seen[0].extensions["timeout"]["read"] == pytest.approx(12.0, abs=0.5)
    assert seen[0].extensions["timeout"]["connect"] == 5.0
    assert seen[1].extensions["timeout"]["read"] == 300


def test_no_failure_quotes_content_or_the_key() -> None:
    for status in (401, 404, 429, 400, 500):
        failure = complete(
            make(lambda _r, s=status: httpx2.Response(s, text=KEY + BODY_MARKER), cloud=True)
        ).failure()
        assert isinstance(failure, Exception)
        assert KEY not in str(failure)
        assert BODY_MARKER not in str(failure)


def _with_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


def test_the_local_provider_never_trusts_the_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_proxy_env(monkeypatch)

    provider = OpenAiCompatibleProvider(
        base_url=LOCAL_URL, cloud=False, max_retries=0, read_timeout_seconds=1.0
    )

    assert provider._http_client._mounts == {}


def test_the_cloud_provider_keeps_the_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_proxy_env(monkeypatch)

    provider = OpenAiCompatibleProvider(
        base_url=CLOUD_URL,
        cloud=True,
        max_retries=0,
        read_timeout_seconds=1.0,
        key_store=FakeKeyStore(),
    )

    assert provider._http_client._mounts


def test_a_cancel_during_repeated_pre_stream_failures_returns_promptly() -> None:
    cancel = Idle()
    seen: list[httpx2.Request] = []
    provider = make(lambda _r: httpx2.Response(503, text="x"), max_retries=6, requests=seen)
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    failure = complete(provider, cancel).failure()

    assert isinstance(failure, SummaryCancelledError)
    assert time.monotonic() - started < 2.0


def test_the_time_left_bounds_repeated_pre_stream_failures() -> None:
    provider = make(lambda _r: httpx2.Response(503, text="x"), max_retries=6)

    started = time.monotonic()
    failure = complete(provider, timeout=0.3).failure()

    assert isinstance(failure, SummaryTimeoutError)
    assert time.monotonic() - started < 2.0


def test_pre_stream_retries_are_counted_by_the_adapter() -> None:
    seen: list[httpx2.Request] = []
    provider = make(
        lambda _r: httpx2.Response(503, headers={"retry-after-ms": "1"}, text="x"),
        max_retries=3,
        requests=seen,
    )

    failure = complete(provider).failure()

    assert isinstance(failure, SummaryProviderUnavailableError)
    assert len(seen) == 4


def test_a_connection_error_is_retried_before_the_stream() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if len(seen) == 1:
            raise httpx2.ConnectError("refused", request=request)
        return sse_response(ok_stream())

    assert isinstance(complete(make(handler, max_retries=1, requests=seen)), Success)
    assert len(seen) == 2
