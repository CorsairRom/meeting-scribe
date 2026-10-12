"""Reads the models a provider offers: Ollama's installed models and an OpenAI-compatible list.

Both are plain GETs with bounded timeouts. Logs name the failure kind or the HTTP status only,
never a model name, a body or the key. The local call never goes through a proxy (``trust_env``
is off); the cloud call carries the key as a bearer token to the configured https base only.
"""

import logging
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx2
from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.errors import (
    SummaryAuthFailedError,
    SummaryError,
    SummaryModelsUnavailableError,
)
from meeting_scribe.summaries.domain.ports import NOT_SUPPORTED_YET, ModelEntry

logger = logging.getLogger(__name__)

# Model-id families the cloud serves on ``/chat/completions``, the only endpoint this version
# calls. The ``/models`` list does not say which endpoint serves a model, so this is the one
# allowlist (source: https://opencode.ai/docs/go, checked 2026-10-11; rules-registry GO-062).
# Anything else (grok, gpt, minimax, claude, qwen, muse, step, hy, omen, space-bunny...) is listed
# as "not supported yet".
CLOUD_CHAT_COMPLETIONS_FAMILIES = ("deepseek-", "glm-", "kimi-", "mimo-", "longcat-")

LOCAL_DEADLINE_SECONDS = 5.0
CLOUD_DEADLINE_SECONDS = 10.0
"""The most a whole listing may take (connect, send, wait and read together)."""
_FIRST_HTTP_ERROR_STATUS, _HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN = 400, 401, 403


def _get(
    client: httpx2.Client, url: str, headers: dict[str, str] | None, deadline_seconds: float
) -> httpx2.Response:
    """One GET that is over within ``deadline_seconds`` in total, not per phase.

    The request runs in a daemon helper thread that the caller abandons when the time is up
    (``httpx2`` timeouts bound each phase, so a trickling server could otherwise hold a ``Set``
    or a window refresh far longer). Raises ``TimeoutError`` at the deadline.
    """
    outcome: list[httpx2.Response | BaseException] = []

    def run() -> None:
        try:
            outcome.append(client.get(url, headers=headers))
        except BaseException as error:
            outcome.append(error)

    worker = threading.Thread(target=run, name="model-list", daemon=True)
    worker.start()
    worker.join(deadline_seconds)
    if not outcome:
        raise TimeoutError("The model list did not arrive in time.")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def _unavailable(message: str) -> Failure[SummaryError]:
    return Failure(SummaryModelsUnavailableError(message))


def _json(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _ids(items: Any, field: str) -> list[str] | None:
    """The distinct, sorted string ``field`` of each dict in ``items``; None for a bad shape."""
    if not isinstance(items, list):
        return None
    found: set[str] = set()
    for item in items:
        value = item.get(field) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            return None
        found.add(value)
    return sorted(found)


class OllamaModelLister:
    """``GET <origin>/api/tags``: the models installed in the local Ollama (a loopback call)."""

    def __init__(
        self,
        base_url: str,
        http_client: httpx2.Client | None = None,
        deadline_seconds: float = LOCAL_DEADLINE_SECONDS,
    ) -> None:
        parts = urlsplit(base_url)
        self._url = f"{parts.scheme}://{parts.netloc}/api/tags"
        self._deadline = deadline_seconds
        # trust_env=False: a loopback call must never be routed through HTTP_PROXY/ALL_PROXY.
        self._client = http_client or httpx2.Client(timeout=deadline_seconds, trust_env=False)

    def list_models(self) -> Result[list[ModelEntry], SummaryError]:
        try:
            response = _get(self._client, self._url, None, self._deadline)
        except Exception as error:  # a stopped Ollama, a timeout
            logger.warning("Ollama model list failed: %s", type(error).__name__)
            return _unavailable("Ollama did not answer. Check that it is running.")
        if response.status_code >= _FIRST_HTTP_ERROR_STATUS:
            logger.warning("Ollama model list answered HTTP %s", response.status_code)
            return _unavailable("Ollama could not list its models.")
        body = _json(response)
        names = _ids(body.get("models"), "name") if isinstance(body, dict) else None
        if names is None:
            return _unavailable("Ollama answered with an unexpected model list.")
        return Success([ModelEntry(id=name) for name in names])


class OpenAiCompatibleModelLister:
    """``GET <base>/models`` with the key: an OpenAI list, ``{"data": [{"id": ...}]}``."""

    def __init__(
        self,
        base_url: str,
        http_client: httpx2.Client | None = None,
        deadline_seconds: float = CLOUD_DEADLINE_SECONDS,
        user_agent: str | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/models"
        self._user_agent = user_agent
        self._deadline = deadline_seconds
        self._client = http_client or httpx2.Client(timeout=deadline_seconds)

    def _headers(self, key: SecretStr) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {key.get_secret_value()}"}
        if self._user_agent:
            headers["User-Agent"] = self._user_agent
        return headers

    def list_models(self, key: SecretStr) -> Result[list[ModelEntry], SummaryError]:
        try:
            response = _get(
                self._client,
                self._url,
                self._headers(key),
                self._deadline,
            )
        except Exception as error:  # offline, a timeout; the message may echo the key: log the type
            logger.warning("Cloud model list failed: %s", type(error).__name__)
            return _unavailable("The cloud provider did not answer. Check the connection.")
        status = response.status_code
        if status in {_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
            return Failure(SummaryAuthFailedError("The cloud provider rejected the API key."))
        if status >= _FIRST_HTTP_ERROR_STATUS:
            logger.warning("Cloud model list answered HTTP %s", status)
            return _unavailable(f"The cloud provider could not list its models (HTTP {status}).")
        body = _json(response)
        ids = _ids(body.get("data"), "id") if isinstance(body, dict) else None
        if ids is None:
            return _unavailable("The cloud provider answered with an unexpected model list.")
        return Success([self._entry(model_id) for model_id in ids])

    @staticmethod
    def _entry(model_id: str) -> ModelEntry:
        if model_id.startswith(CLOUD_CHAT_COMPLETIONS_FAMILIES):
            return ModelEntry(id=model_id)
        return ModelEntry(id=model_id, supported=False, reason=NOT_SUPPORTED_YET)
