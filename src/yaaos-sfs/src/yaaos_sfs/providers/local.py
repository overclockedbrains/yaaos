"""Local embedding provider using sentence-transformers with GPU/multi-core support."""

from __future__ import annotations

import logging
import os

from sentence_transformers import SentenceTransformer

from . import EmbeddingProvider

log = logging.getLogger("yaaos-sfs")


def _detect_device() -> str:
    """Auto-detect the best available device for inference."""
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            log.info(f"CUDA GPU detected: {name} ({vram:.1f} GB VRAM)")
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            log.info("Apple MPS GPU detected")
            return "mps"
    except ImportError:
        pass

    cores = os.cpu_count() or 1
    log.info(f"No GPU detected, using CPU ({cores} cores)")
    return "cpu"


class LocalEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using a local sentence-transformers model.

    Automatically detects and uses GPU (CUDA/MPS) if available,
    falls back to CPU with multi-threaded inference.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", device: str | None = None):
        self._model_name = model_name
        self._device = device or _detect_device()
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            log.info(f"Loading model '{self._model_name}' on {self._device}...")
            self._model = SentenceTransformer(self._model_name, device=self._device)
            log.info(f"Model loaded ({self.dims} dims, device={self._device})")
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        # GPU can handle larger batches; CPU benefits from smaller batches
        batch_size = 64 if self._device != "cpu" else 32
        embeddings = self.model.encode(
            texts,
            show_progress_bar=False,
            normalize_embeddings=True,
            batch_size=batch_size,
        )
        return [e.tolist() for e in embeddings]

    def embed_query(self, query: str) -> list[float]:
        return self.model.encode(
            query,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

    @property
    def dims(self) -> int:
        return self.model.get_sentence_embedding_dimension()
