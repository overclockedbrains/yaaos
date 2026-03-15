"""Model Bus embedding provider — routes embeddings through the YAAOS Model Bus daemon.

Instead of calling embedding models directly, this provider sends requests to the
Model Bus Unix socket, which handles provider selection, resource management,
and model lifecycle. This is the recommended provider for production YAAOS deployments.
"""

from __future__ import annotations

from . import EmbeddingProvider

# Known dims for common Model Bus models (used when Bus is unreachable at init)
_KNOWN_DIMS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "all-MiniLM-L6-v2": 384,
    "snowflake-arctic-embed": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "voyage-code-3": 1024,
    "voyage-3": 1024,
    "voyage-3-lite": 512,
}


class ModelBusEmbeddingProvider(EmbeddingProvider):
    """Embedding provider that routes through the YAAOS Model Bus daemon.

    Uses the synchronous ModelBusClient to send embed requests over Unix socket.
    The Model Bus handles provider selection, VRAM management, and model lifecycle.
    """

    def __init__(
        self,
        model: str | None = None,
        socket_path: str | None = None,
        embedding_dims: int | None = None,
    ):
        """Initialize the Model Bus embedding provider.

        Args:
            model: Model string (e.g., "ollama/nomic-embed-text"). None uses Bus default.
            socket_path: Override Unix socket path. None uses default discovery.
            embedding_dims: Override embedding dimensions. None probes from Bus.
        """
        try:
            from yaaos_modelbus.client import ModelBusClient
        except ImportError:
            raise ImportError(
                "Model Bus provider requires the 'yaaos-modelbus' package. "
                "Install with: uv pip install yaaos-modelbus"
            )

        self._model = model
        self._client = ModelBusClient(socket_path)
        self._dims_cache: int | None = embedding_dims

        # If dims not provided, try to determine from known models or probe
        if self._dims_cache is None and model:
            # Strip provider prefix for lookup
            model_name = model.split("/")[-1] if "/" in model else model
            self._dims_cache = _KNOWN_DIMS.get(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Model Bus."""
        result = self._client.embed(texts, model=self._model)
        embeddings = result.get("embeddings", [])

        # Cache dims from response
        if self._dims_cache is None and result.get("dims"):
            self._dims_cache = result["dims"]
        elif self._dims_cache is None and embeddings:
            self._dims_cache = len(embeddings[0])

        return embeddings

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a single query via Model Bus."""
        result = self._client.embed([query], model=self._model)
        embeddings = result.get("embeddings", [])

        if self._dims_cache is None and result.get("dims"):
            self._dims_cache = result["dims"]
        elif self._dims_cache is None and embeddings:
            self._dims_cache = len(embeddings[0])

        return embeddings[0] if embeddings else []

    @property
    def dims(self) -> int:
        """Return embedding dimensions.

        If not cached, probes the Model Bus by embedding a single token.
        """
        if self._dims_cache is not None:
            return self._dims_cache

        # Probe by embedding a single token
        result = self._client.embed(["hello"], model=self._model)
        if result.get("dims"):
            self._dims_cache = result["dims"]
            return self._dims_cache

        embeddings = result.get("embeddings", [])
        if embeddings:
            self._dims_cache = len(embeddings[0])
            return self._dims_cache

        raise RuntimeError(
            "Cannot determine embedding dimensions from Model Bus. "
            "Ensure the Model Bus daemon is running and a model is available."
        )
