"""Voyage AI embedding provider — best-in-class code embeddings (voyage-code-3)."""

from __future__ import annotations

import os

from . import EmbeddingProvider

# Dimension map for known Voyage models
VOYAGE_DIMS = {
    "voyage-code-3": 1024,
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-code-2": 1536,
}


class VoyageEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using Voyage AI's API."""

    def __init__(self, api_key: str | None = None, model: str = "voyage-code-3"):
        try:
            import voyageai
        except ImportError:
            raise ImportError(
                "Voyage provider requires the 'voyageai' package. "
                "Install with: uv pip install voyageai"
            )

        key = api_key or os.environ.get("VOYAGE_API_KEY", "")
        if not key:
            raise ValueError(
                "Voyage provider requires an API key. Set VOYAGE_API_KEY env var "
                "or configure api_key in config.toml under [providers.voyage]"
            )

        self._client = voyageai.Client(api_key=key)
        self._model = model
        self._dims = VOYAGE_DIMS.get(model, 1024)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Voyage API supports up to 128 texts per batch
        if len(texts) > 128:
            all_embeddings = []
            for i in range(0, len(texts), 128):
                batch = texts[i : i + 128]
                result = self._client.embed(batch, model=self._model, input_type="document")
                all_embeddings.extend(result.embeddings)
            return all_embeddings

        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings

    def embed_query(self, query: str) -> list[float]:
        result = self._client.embed([query], model=self._model, input_type="query")
        return result.embeddings[0]

    @property
    def dims(self) -> int:
        return self._dims
