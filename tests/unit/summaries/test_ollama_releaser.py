import json

import httpx2
import pytest

from meeting_scribe.summaries.infrastructure.ollama_releaser import OllamaModelReleaser


def make(handler, seen: list[httpx2.Request]) -> OllamaModelReleaser:
    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    return OllamaModelReleaser(
        base_url="http://127.0.0.1:11434/v1",
        model="model-x",
        http_client=httpx2.Client(transport=httpx2.MockTransport(record)),
    )


def test_release_after_local_use() -> None:
    seen: list[httpx2.Request] = []

    make(lambda _r: httpx2.Response(200, json={"done": True}), seen).release()

    assert len(seen) == 1
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "http://127.0.0.1:11434/api/generate")
    assert json.loads(request.content) == {"model": "model-x", "keep_alive": 0}


def test_release_never_raises_and_logs_only_the_failure_kind(caplog) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    make(refuse, []).release()

    assert "ConnectError" in caplog.text
    assert "model-x" not in caplog.text


def test_an_error_status_is_swallowed_too(caplog) -> None:
    make(lambda _r: httpx2.Response(500, text="x"), []).release()

    assert "500" in caplog.text


def test_the_releaser_never_trusts_the_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")

    releaser = OllamaModelReleaser(base_url="http://127.0.0.1:11434/v1", model="model-x")

    assert releaser._client._mounts == {}
