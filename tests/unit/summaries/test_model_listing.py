"""Model listing: Ollama tags, the provider's /models, the cloud allowlist and the use case."""

import httpx2
import pytest
from pydantic import SecretStr
from returns.result import Failure, Success

from meeting_scribe.summaries.application.list_models import ListModels
from meeting_scribe.summaries.domain.errors import (
    SummaryAuthFailedError,
    SummaryCloudKeyMissingError,
    SummaryModelsUnavailableError,
)
from meeting_scribe.summaries.domain.ports import NOT_SUPPORTED_YET, ModelEntry
from meeting_scribe.summaries.infrastructure.model_listing import (
    CLOUD_CHAT_COMPLETIONS_FAMILIES,
    OllamaModelLister,
    OpenAiCompatibleModelLister,
)

KEY = "sk-synthetic-key-123"
CLOUD_BASE = "https://gateway.example.test/zen/go/v1"


def client(handler, seen: list[httpx2.Request] | None = None) -> httpx2.Client:
    def record(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx2.Client(transport=httpx2.MockTransport(record))


def listing(*ids: str) -> dict:
    return {
        "object": "list",
        "data": [{"id": i, "object": "model", "created": 1, "owned_by": "x"} for i in ids],
    }


# -- Ollama -------------------------------------------------------------------------------


def test_the_local_list_reads_the_installed_models_from_the_loopback_tags() -> None:
    seen: list[httpx2.Request] = []
    body = {"models": [{"name": "qwen3.5:9b"}, {"name": "llama3:8b"}]}
    lister = OllamaModelLister(
        "http://127.0.0.1:11434/v1", client(lambda _r: httpx2.Response(200, json=body), seen)
    )

    result = lister.list_models()

    assert result == Success([ModelEntry(id="llama3:8b"), ModelEntry(id="qwen3.5:9b")])
    assert (seen[0].method, str(seen[0].url)) == ("GET", "http://127.0.0.1:11434/api/tags")


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(500, text="x"),
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, json={"models": "nope"}),
        httpx2.Response(200, json=["a"]),
    ],
)
def test_a_bad_local_answer_is_an_unavailable_list(response: httpx2.Response) -> None:
    lister = OllamaModelLister("http://127.0.0.1:11434/v1", client(lambda _r: response))

    result = lister.list_models()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), SummaryModelsUnavailableError)


def test_an_unreachable_ollama_is_unavailable_and_logs_the_kind_only(caplog) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    result = OllamaModelLister("http://127.0.0.1:11434/v1", client(refuse)).list_models()

    assert isinstance(result.failure(), SummaryModelsUnavailableError)
    assert "ConnectError" in caplog.text


def test_the_local_lister_never_trusts_the_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")

    lister = OllamaModelLister("http://127.0.0.1:11434/v1")

    assert lister._client._mounts == {}


# -- the cloud provider -------------------------------------------------------------------


def test_the_cloud_list_sends_the_key_as_a_bearer_token_to_the_models_url() -> None:
    seen: list[httpx2.Request] = []
    lister = OpenAiCompatibleModelLister(
        CLOUD_BASE + "/", client(lambda _r: httpx2.Response(200, json=listing("glm-5")), seen)
    )

    result = lister.list_models(SecretStr(KEY))

    assert isinstance(result, Success)
    assert (seen[0].method, str(seen[0].url)) == ("GET", CLOUD_BASE + "/models")
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_the_cloud_allowlist() -> None:
    assert CLOUD_CHAT_COMPLETIONS_FAMILIES == ("deepseek-", "glm-", "kimi-", "mimo-", "longcat-")
    ids = [
        "deepseek-v4-pro", "glm-5", "kimi-k2.5", "mimo-v2", "longcat-flash",
        "grok-4", "gpt-5", "minimax-m2", "claude-sonnet", "qwen3-max", "muse-1", "step-3",
        "hy3", "omen-1", "space-bunny",
    ]  # fmt: skip
    lister = OpenAiCompatibleModelLister(
        CLOUD_BASE, client(lambda _r: httpx2.Response(200, json=listing(*ids)))
    )

    rows = lister.list_models(SecretStr(KEY)).unwrap()

    assert len(rows) == len(ids)  # every model is listed
    supported = {row.id for row in rows if row.supported}
    assert supported == set(ids[:5])
    unsupported = [row for row in rows if not row.supported]
    assert {row.reason for row in unsupported} == {NOT_SUPPORTED_YET}
    assert all(row.reason == "" for row in rows if row.supported)


def test_cloud_entries_are_sorted_and_deduplicated() -> None:
    lister = OpenAiCompatibleModelLister(
        CLOUD_BASE,
        client(lambda _r: httpx2.Response(200, json=listing("kimi-b", "glm-a", "kimi-b"))),
    )

    assert [r.id for r in lister.list_models(SecretStr(KEY)).unwrap()] == ["glm-a", "kimi-b"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_an_auth_failure(status: int) -> None:
    lister = OpenAiCompatibleModelLister(CLOUD_BASE, client(lambda _r: httpx2.Response(status)))

    assert isinstance(lister.list_models(SecretStr(KEY)).failure(), SummaryAuthFailedError)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(500, text="x"),
        httpx2.Response(429, text="x"),
        httpx2.Response(200, text="<html>"),
        httpx2.Response(200, json={"data": "nope"}),
        httpx2.Response(200, json={"data": [{"id": 3}]}),
    ],
)
def test_a_bad_cloud_answer_is_an_unavailable_list(response: httpx2.Response) -> None:
    lister = OpenAiCompatibleModelLister(CLOUD_BASE, client(lambda _r: response))

    assert isinstance(lister.list_models(SecretStr(KEY)).failure(), SummaryModelsUnavailableError)


def test_the_key_is_never_logged_or_in_an_error(caplog) -> None:
    def explode(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(f"slow {KEY}", request=request)

    lister = OpenAiCompatibleModelLister(CLOUD_BASE, client(explode))

    failure = lister.list_models(SecretStr(KEY)).failure()

    assert KEY not in caplog.text
    assert KEY not in str(failure)
    assert "ReadTimeout" in caplog.text


# -- the use case -------------------------------------------------------------------------


class FakeLocal:
    def list_models(self):
        return Success([ModelEntry(id="m1")])


class FakeCloud:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def list_models(self, key: SecretStr):
        self.keys.append(key.get_secret_value())
        return Success([ModelEntry(id="glm-5")])


class FakeKeys:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    def get_key(self):
        self.calls += 1
        return self.result


def use_case(keys: FakeKeys, cloud: FakeCloud | None = None) -> ListModels:
    return ListModels(FakeLocal(), cloud or FakeCloud(), keys)


def test_the_local_scope_needs_no_key() -> None:
    keys = FakeKeys(Failure(SummaryCloudKeyMissingError("x")))

    assert use_case(keys).execute("local") == Success([ModelEntry(id="m1")])
    assert keys.calls == 0


def test_the_cloud_scope_reads_the_key_for_the_request_only() -> None:
    cloud = FakeCloud()
    keys = FakeKeys(Success(SecretStr(KEY)))

    result = use_case(keys, cloud).execute("cloud")

    assert result == Success([ModelEntry(id="glm-5")])
    assert cloud.keys == [KEY]
    assert keys.calls == 1


def test_a_missing_key_is_the_cloud_status_and_nothing_is_called() -> None:
    cloud = FakeCloud()
    keys = FakeKeys(Failure(SummaryCloudKeyMissingError("x")))

    result = use_case(keys, cloud).execute("cloud")

    assert isinstance(result.failure(), SummaryCloudKeyMissingError)
    assert cloud.keys == []


def test_an_unknown_scope_is_unavailable() -> None:
    result = use_case(FakeKeys(Success(SecretStr(KEY)))).execute("other")

    assert isinstance(result.failure(), SummaryModelsUnavailableError)
