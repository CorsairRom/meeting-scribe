"""Frees the local model from the GPU after a summary (spec 5, AC-12).

The OpenAI-compatible endpoint has no way to unload a model, so this makes the one native Ollama
call that does: ``POST <origin>/api/generate {"model": ..., "keep_alive": 0}``. It is a loopback
call, best effort, and logs only the kind of failure, never the model name or any content.
"""

import logging
from urllib.parse import urlsplit

import httpx2

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0
_ERROR_STATUS = 400


class OllamaModelReleaser:
    def __init__(
        self,
        base_url: str,
        model: str,
        http_client: httpx2.Client | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        parts = urlsplit(base_url)
        self._url = f"{parts.scheme}://{parts.netloc}/api/generate"
        self._model = model
        # trust_env=False: a loopback call must never be routed through HTTP_PROXY/ALL_PROXY.
        self._client = http_client or httpx2.Client(timeout=timeout_seconds, trust_env=False)

    def release(self) -> None:
        """Ask Ollama to unload the model now. Never raises."""
        try:
            response = self._client.post(self._url, json={"model": self._model, "keep_alive": 0})
        except Exception as error:  # best effort: a stopped Ollama has nothing to release
            logger.warning("Ollama model release failed: %s", type(error).__name__)
            return
        if response.status_code >= _ERROR_STATUS:
            logger.warning("Ollama model release answered HTTP %s", response.status_code)
