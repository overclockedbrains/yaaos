"""Ollama provider — local AI inference via Ollama REST API.

Ollama must be running as a service (ollama.service) or manually started.
Supports embedding, generation, and chat via its HTTP API.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
import orjson
import structlog

from yaaos_modelbus.types import Chunk, EmbedResult, Message, ModelInfo, ProviderHealth

logger = structlog.get_logger()

# Known embedding dimensions for common Ollama models
_KNOWN_EMBED_DIMS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "all-minilm:l6-v2": 384,
    "snowflake-arctic-embed": 1024,
}

# Known parameter counts for common models
_KNOWN_PARAMS: dict[str, float] = {
    "phi3:mini": 3.8,
    "phi3": 3.8,
    "llama3.2": 3.0,
    "llama3.2:3b": 3.0,
    "llama3.2:1b": 1.0,
    "mistral": 7.0,
    "qwen2:1.5b": 1.5,
    "qwen2:7b": 7.0,
    "gemma2:2b": 2.0,
}


class OllamaProvider:
    """Ollama REST API provider for local inference."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
        )

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        """Generate embeddings via Ollama /api/embed."""
        response = await self._client.post(
            "/api/embed",
            json={"model": model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()

        embeddings = data.get("embeddings", [])
        dims = len(embeddings[0]) if embeddings else 0

        return EmbedResult(
            embeddings=embeddings,
            model=f"ollama/{model}",
            dims=dims,
            usage={"prompt_tokens": data.get("prompt_eval_count", 0)},
        )

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream text generation via Ollama /api/generate (NDJSON)."""
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system
        if stop:
            payload["options"]["stop"] = stop

        async with self._client.stream("POST", "/api/generate", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = orjson.loads(line)
                if data.get("done"):
                    yield Chunk(
                        token="",
                        done=True,
                        usage={
                            "prompt_tokens": data.get("prompt_eval_count", 0),
                            "completion_tokens": data.get("eval_count", 0),
                        },
                    )
                else:
                    yield Chunk(token=data.get("response", ""))

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream chat completion via Ollama /api/chat (NDJSON)."""
        payload: dict = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if stop:
            payload["options"]["stop"] = stop

        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = orjson.loads(line)
                if data.get("done"):
                    yield Chunk(
                        token="",
                        done=True,
                        usage={
                            "prompt_tokens": data.get("prompt_eval_count", 0),
                            "completion_tokens": data.get("eval_count", 0),
                        },
                    )
                else:
                    msg = data.get("message", {})
                    yield Chunk(token=msg.get("content", ""))

    async def list_models(self) -> list[ModelInfo]:
        """List models available in Ollama."""
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("ollama.list_models_failed", error=str(e))
            return []

        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            details = m.get("details", {})

            # Determine capabilities
            family = details.get("family", "").lower()
            base_name = name.split(":")[0]
            is_embed = "embed" in base_name or "embed" in family
            capabilities = ["embed"] if is_embed else ["generate", "chat"]

            # Look up known metadata
            dims = _KNOWN_EMBED_DIMS.get(base_name) or _KNOWN_EMBED_DIMS.get(name)
            params = _KNOWN_PARAMS.get(base_name) or _KNOWN_PARAMS.get(name)
            quant = details.get("quantization_level")

            models.append(
                ModelInfo(
                    id=f"ollama/{name}",
                    provider="ollama",
                    name=name,
                    capabilities=capabilities,
                    params_billions=params,
                    quantization=quant,
                    embedding_dims=dims,
                )
            )

        return models

    async def health(self) -> ProviderHealth:
        """Check if Ollama is running and responsive."""
        try:
            response = await self._client.get("/", timeout=5.0)
            is_healthy = response.status_code == 200

            # Get loaded models
            models_loaded = []
            try:
                ps_resp = await self._client.get("/api/ps", timeout=5.0)
                if ps_resp.status_code == 200:
                    ps_data = ps_resp.json()
                    models_loaded = [m.get("name", "") for m in ps_data.get("models", [])]
            except Exception:
                pass

            return ProviderHealth(
                name="ollama",
                healthy=is_healthy,
                models_loaded=models_loaded,
            )
        except Exception as e:
            return ProviderHealth(
                name="ollama",
                healthy=False,
                error=str(e),
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
