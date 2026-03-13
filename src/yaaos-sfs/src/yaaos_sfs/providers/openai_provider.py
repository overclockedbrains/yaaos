"""OpenAI embedding provider — optional cloud provider for testing with high-end models."""

from __future__ import annotations

from . import EmbeddingProvider

# Dimension map for known OpenAI models
OPENAI_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using OpenAI's API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI provider requires the 'openai' package. "
                "Install with: uv pip install 'yaaos-sfs[openai]'"
            )
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._dims = OPENAI_DIMS.get(model, 1536)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]

    def embed_query(self, query: str) -> list[float]:
        response = self._client.embeddings.create(input=[query], model=self._model)
        return response.data[0].embedding

    @property
    def dims(self) -> int:
        return self._dims
