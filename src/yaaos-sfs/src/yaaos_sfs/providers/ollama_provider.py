"""Ollama embedding provider — local models via Ollama API.

Bridges to any embedding model served by Ollama (e.g. nomic-embed-text, mxbai-embed-large).
This bridges nicely to the Model Bus planned for Phase 2.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

from . import EmbeddingProvider

# Known model dimensions (Ollama doesn't always expose this)
OLLAMA_DIMS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
}


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using Ollama's local API."""

    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dims_cache: int | None = OLLAMA_DIMS.get(model)

        # Verify connectivity
        try:
            self._api_request("api/tags", method="GET")
        except Exception as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {base_url}. "
                f"Make sure Ollama is running: ollama serve\n"
                f"Error: {e}"
            )

    def _api_request(self, endpoint: str, data: dict | None = None, method: str = "POST") -> dict:
        """Make a request to the Ollama API."""
        url = f"{self._base_url}/{endpoint}"

        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method=method)
            req.add_header("Content-Type", "application/json")
        else:
            req = urllib.request.Request(url, method=method)

        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Ollama embedding endpoint handles batch natively
        result = self._api_request("api/embed", {"model": self._model, "input": texts})
        embeddings = result.get("embeddings", [])

        # Cache dimensions from first response
        if self._dims_cache is None and embeddings:
            self._dims_cache = len(embeddings[0])

        return embeddings

    def embed_query(self, query: str) -> list[float]:
        result = self._api_request("api/embed", {"model": self._model, "input": [query]})
        embeddings = result.get("embeddings", [])

        if self._dims_cache is None and embeddings:
            self._dims_cache = len(embeddings[0])

        return embeddings[0] if embeddings else []

    @property
    def dims(self) -> int:
        if self._dims_cache is not None:
            return self._dims_cache

        # Probe by embedding a single token
        result = self._api_request("api/embed", {"model": self._model, "input": ["hello"]})
        embeddings = result.get("embeddings", [])
        if embeddings:
            self._dims_cache = len(embeddings[0])
            return self._dims_cache

        raise RuntimeError(f"Cannot determine embedding dimensions for model {self._model}")
