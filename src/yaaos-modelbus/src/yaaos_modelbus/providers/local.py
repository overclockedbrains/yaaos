"""Local provider — direct sentence-transformers embedding without Ollama.

Requires: pip install yaaos-modelbus[local]
No server needed — loads model directly into process.
"""

from __future__ import annotations

from typing import AsyncIterator

from yaaos_modelbus.types import Chunk, EmbedResult, ModelInfo, ProviderHealth

try:
    from sentence_transformers import SentenceTransformer

    _HAS_ST = True
except ImportError:
    _HAS_ST = False


class LocalProvider:
    """Direct local embedding via sentence-transformers."""

    name = "local"

    def __init__(self, device: str | None = None):
        if not _HAS_ST:
            raise ImportError(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            )
        self._device = device or self._detect_device()
        self._models: dict[str, SentenceTransformer] = {}

    def _detect_device(self) -> str:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _get_model_sync(self, model_name: str) -> SentenceTransformer:
        if model_name not in self._models:
            self._models[model_name] = SentenceTransformer(model_name, device=self._device)
        return self._models[model_name]

    async def _get_model(self, model_name: str) -> SentenceTransformer:
        """Get or load a model, running the load in a thread to avoid blocking the event loop."""
        if model_name in self._models:
            return self._models[model_name]
        import asyncio

        model = await asyncio.to_thread(self._get_model_sync, model_name)
        return model

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        model_name = model or "all-MiniLM-L6-v2"
        st_model = await self._get_model(model_name)

        import asyncio

        embeddings = await asyncio.to_thread(st_model.encode, texts, show_progress_bar=False)
        embedding_lists = [e.tolist() for e in embeddings]
        dims = len(embedding_lists[0]) if embedding_lists else 0

        return EmbedResult(
            embeddings=embedding_lists,
            model=f"local/{model_name}",
            dims=dims,
        )

    async def generate(self, model, prompt, **kwargs) -> AsyncIterator[Chunk]:
        raise NotImplementedError("Local provider only supports embeddings")

    async def chat(self, model, messages, **kwargs) -> AsyncIterator[Chunk]:
        raise NotImplementedError("Local provider only supports embeddings")

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="local/all-MiniLM-L6-v2",
                provider="local",
                name="all-MiniLM-L6-v2",
                capabilities=["embed"],
                embedding_dims=384,
            ),
        ]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            name="local",
            healthy=True,
            models_loaded=list(self._models.keys()),
        )
