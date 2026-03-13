"""Local embedding provider using sentence-transformers."""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from . import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using a local sentence-transformers model."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, show_progress_bar=False)
        return [e.tolist() for e in embeddings]

    def embed_query(self, query: str) -> list[float]:
        return self.model.encode(query, show_progress_bar=False).tolist()

    @property
    def dims(self) -> int:
        return self.model.get_sentence_embedding_dimension()
