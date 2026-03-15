"""Voyage AI provider — embedding-only cloud provider.

Requires: pip install yaaos-modelbus[voyage]
API key via env: VOYAGE_API_KEY
"""

from __future__ import annotations

from typing import AsyncIterator

from yaaos_modelbus.types import Chunk, EmbedResult, ModelInfo, ProviderHealth

try:
    import voyageai

    _HAS_VOYAGE = True
except ImportError:
    _HAS_VOYAGE = False


class VoyageProvider:
    """Voyage AI embedding provider."""

    name = "voyage"

    def __init__(self, api_key: str):
        if not _HAS_VOYAGE:
            raise ImportError("voyageai package not installed. Run: pip install voyageai")
        self._client = voyageai.AsyncClient(api_key=api_key)

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        result = await self._client.embed(
            texts,
            model=model or "voyage-3.5",
            input_type="document",
        )
        embeddings = result.embeddings
        dims = len(embeddings[0]) if embeddings else 0

        return EmbedResult(
            embeddings=embeddings,
            model=f"voyage/{model}",
            dims=dims,
            usage={"total_tokens": result.total_tokens},
        )

    async def generate(self, model, prompt, **kwargs) -> AsyncIterator[Chunk]:
        raise NotImplementedError("Voyage only supports embeddings")

    async def chat(self, model, messages, **kwargs) -> AsyncIterator[Chunk]:
        raise NotImplementedError("Voyage only supports embeddings")

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="voyage/voyage-3.5",
                provider="voyage",
                name="voyage-3.5",
                capabilities=["embed"],
                embedding_dims=1024,
            ),
            ModelInfo(
                id="voyage/voyage-code-3",
                provider="voyage",
                name="voyage-code-3",
                capabilities=["embed"],
                embedding_dims=1024,
            ),
        ]

    async def health(self) -> ProviderHealth:
        try:
            await self._client.embed(["health check"], model="voyage-3.5", input_type="query")
            return ProviderHealth(name="voyage", healthy=True)
        except Exception as e:
            return ProviderHealth(name="voyage", healthy=False, error=str(e))
