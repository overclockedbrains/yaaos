"""Core data types for the Model Bus."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Message:
    """A chat message."""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(role=d["role"], content=d["content"])


@dataclass(slots=True)
class Chunk:
    """A streaming generation chunk."""

    token: str
    done: bool = False
    usage: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {"token": self.token, "done": self.done}
        if self.usage is not None:
            d["usage"] = self.usage
        return d


@dataclass(frozen=True, slots=True)
class EmbedResult:
    """Result of an embedding request."""

    embeddings: list[list[float]]
    model: str
    dims: int
    usage: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "embeddings": self.embeddings,
            "model": self.model,
            "dims": self.dims,
        }
        if self.usage is not None:
            d["usage"] = self.usage
        return d


@dataclass(frozen=True, slots=True)
class GenerateResult:
    """Final result of a generation request."""

    text: str
    model: str
    usage: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {"text": self.text, "model": self.model}
        if self.usage is not None:
            d["usage"] = self.usage
        return d


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Information about an available model."""

    id: str  # "ollama/phi3:mini"
    provider: str  # "ollama"
    name: str  # "phi3:mini"
    capabilities: list[str] = field(default_factory=list)  # ["generate", "chat"] or ["embed"]
    params_billions: float | None = None
    quantization: str | None = None  # "Q4_K_M"
    estimated_vram_mb: int | None = None
    context_length: int | None = None
    embedding_dims: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "provider": self.provider,
            "name": self.name,
            "capabilities": self.capabilities,
        }
        if self.params_billions is not None:
            d["params_billions"] = self.params_billions
        if self.quantization is not None:
            d["quantization"] = self.quantization
        if self.estimated_vram_mb is not None:
            d["estimated_vram_mb"] = self.estimated_vram_mb
        if self.context_length is not None:
            d["context_length"] = self.context_length
        if self.embedding_dims is not None:
            d["embedding_dims"] = self.embedding_dims
        return d


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Health status of a provider."""

    name: str
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
    models_loaded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "healthy": self.healthy}
        if self.latency_ms is not None:
            d["latency_ms"] = self.latency_ms
        if self.error is not None:
            d["error"] = self.error
        if self.models_loaded:
            d["models_loaded"] = self.models_loaded
        return d


@dataclass(frozen=True, slots=True)
class ResourceStatus:
    """Current resource usage."""

    gpu_name: str | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    ram_total_mb: int = 0
    ram_available_mb: int = 0

    def to_dict(self) -> dict:
        d: dict = {}
        if self.gpu_name is not None:
            d["gpu"] = {
                "name": self.gpu_name,
                "vram_total_mb": self.vram_total_mb,
                "vram_free_mb": self.vram_free_mb,
            }
        d["ram"] = {
            "total_mb": self.ram_total_mb,
            "available_mb": self.ram_available_mb,
        }
        return d


def parse_model_string(model: str) -> tuple[str, str]:
    """Parse 'provider/model' string into (provider, model_name).

    If no '/' prefix, returns ("", model) meaning 'use default provider'.
    """
    if "/" in model:
        provider, _, name = model.partition("/")
        return provider, name
    return "", model
