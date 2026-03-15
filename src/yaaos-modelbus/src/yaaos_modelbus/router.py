"""Request router — parses provider/model strings and dispatches to providers.

The router is the bridge between the JSON-RPC server and the provider registry.
It parses model strings like "ollama/phi3:mini", resolves defaults, and
dispatches embed/generate/chat requests to the correct provider.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import structlog

from yaaos_modelbus.config import Config
from yaaos_modelbus.errors import (
    InvalidParamsError,
    ProviderUnavailableError,
)
from yaaos_modelbus.types import EmbedResult, parse_model_string

logger = structlog.get_logger()


class Router:
    """Routes inference requests to the appropriate provider."""

    def __init__(
        self,
        config: Config,
        registry: dict | None = None,
        resource_manager=None,
        unload_callback=None,
    ):
        self.config = config
        # Registry maps provider name → provider instance
        # Will be populated by the daemon during startup
        self._registry: dict = registry or {}
        self._resource_mgr = resource_manager
        self._unload_callback = unload_callback

    def set_registry(self, registry: dict) -> None:
        self._registry = registry

    def resolve_model(self, model: str | None, capability: str) -> tuple[str, str]:
        """Resolve a model string to (provider_name, model_name).

        If model is None or empty, uses the configured default for the capability.
        If model has no provider prefix, uses the default provider.
        """
        if not model:
            model = self.config.get_default_model(capability)

        provider_name, model_name = parse_model_string(model)

        if not provider_name:
            # No prefix — infer provider from default model's prefix
            default = self.config.get_default_model(capability)
            provider_name, _ = parse_model_string(default)
            if not provider_name:
                provider_name = "ollama"  # ultimate fallback

        return provider_name, model_name

    def get_provider(self, provider_name: str):
        """Get a provider by name, raising if unavailable."""
        if provider_name not in self._registry:
            raise ProviderUnavailableError(
                f"Provider '{provider_name}' is not registered. "
                f"Available: {list(self._registry.keys())}"
            )

        provider = self._registry[provider_name]

        # Check if provider is enabled in config
        prov_config = self.config.providers.get(provider_name)
        if prov_config and not prov_config.enabled:
            raise ProviderUnavailableError(f"Provider '{provider_name}' is disabled in config")

        return provider

    async def handle_embed(self, params: dict) -> dict:
        """Handle an embed request."""
        texts = params.get("texts")
        if not texts or not isinstance(texts, list):
            raise InvalidParamsError("'texts' must be a non-empty list of strings")

        model = params.get("model")
        provider_name, model_name = self.resolve_model(model, "embed")
        provider = self.get_provider(provider_name)

        # Track resource usage and ensure capacity for local providers
        model_id = f"{provider_name}/{model_name}"
        if self._resource_mgr:
            self._resource_mgr.touch_model(model_id)
            if provider_name in ("ollama", "local") and self._unload_callback:
                await self._resource_mgr.ensure_capacity(model_id, self._unload_callback)

        start = time.monotonic()
        result: EmbedResult = await provider.embed(model_name, texts)
        elapsed = time.monotonic() - start

        # Register model if not tracked yet
        if self._resource_mgr:
            self._resource_mgr.register_model(model_id, provider_name)

        logger.info(
            "embed.completed",
            provider=provider_name,
            model=model_name,
            texts=len(texts),
            dims=result.dims,
            elapsed_ms=round(elapsed * 1000, 1),
        )

        return result.to_dict()

    async def handle_generate(self, params: dict) -> AsyncIterator[dict]:
        """Handle a generate request (streaming)."""
        prompt = params.get("prompt")
        if not prompt or not isinstance(prompt, str):
            raise InvalidParamsError("'prompt' must be a non-empty string")

        model = params.get("model")
        provider_name, model_name = self.resolve_model(model, "generate")
        provider = self.get_provider(provider_name)

        model_id = f"{provider_name}/{model_name}"
        if self._resource_mgr:
            if provider_name in ("ollama", "local") and self._unload_callback:
                await self._resource_mgr.ensure_capacity(model_id, self._unload_callback)
            self._resource_mgr.register_model(model_id, provider_name)

        full_text_parts: list[str] = []
        final_usage = None

        async for chunk in provider.generate(
            model_name,
            prompt,
            system=params.get("system"),
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens", 2048),
            stop=params.get("stop"),
        ):
            if chunk.done:
                final_usage = chunk.usage
                break
            full_text_parts.append(chunk.token)
            yield chunk.to_dict()

        # Yield final result with done=True
        yield {
            "done": True,
            "text": "".join(full_text_parts),
            "model": f"{provider_name}/{model_name}",
            "usage": final_usage,
        }

    async def handle_chat(self, params: dict) -> AsyncIterator[dict]:
        """Handle a chat request (streaming)."""
        messages = params.get("messages")
        if not messages or not isinstance(messages, list):
            raise InvalidParamsError("'messages' must be a non-empty list")

        model = params.get("model")
        provider_name, model_name = self.resolve_model(model, "chat")
        provider = self.get_provider(provider_name)

        model_id = f"{provider_name}/{model_name}"
        if self._resource_mgr:
            if provider_name in ("ollama", "local") and self._unload_callback:
                await self._resource_mgr.ensure_capacity(model_id, self._unload_callback)
            self._resource_mgr.register_model(model_id, provider_name)

        from yaaos_modelbus.types import Message

        parsed_messages = [Message.from_dict(m) for m in messages]
        full_text_parts: list[str] = []
        final_usage = None

        async for chunk in provider.chat(
            model_name,
            parsed_messages,
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens", 2048),
            stop=params.get("stop"),
        ):
            if chunk.done:
                final_usage = chunk.usage
                break
            full_text_parts.append(chunk.token)
            yield chunk.to_dict()

        yield {
            "done": True,
            "text": "".join(full_text_parts),
            "model": f"{provider_name}/{model_name}",
            "usage": final_usage,
        }

    async def handle_models_list(self, params: dict) -> dict:
        """Handle models.list — aggregate models from all providers."""
        all_models: list[dict] = []

        for name, provider in self._registry.items():
            prov_config = self.config.providers.get(name)
            if prov_config and not prov_config.enabled:
                continue
            try:
                models = await provider.list_models()
                for m in models:
                    all_models.append(m.to_dict())
            except Exception as e:
                logger.warning("models.list_error", provider=name, error=str(e))

        return {"models": all_models}

    async def handle_health(self, params: dict) -> dict:
        """Handle health — check all providers and resources."""
        provider_health = {}

        for name, provider in self._registry.items():
            prov_config = self.config.providers.get(name)
            if prov_config and not prov_config.enabled:
                continue
            try:
                start = time.monotonic()
                health = await provider.health()
                health_dict = health.to_dict()
                health_dict["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
                provider_health[name] = health_dict
            except Exception as e:
                provider_health[name] = {
                    "name": name,
                    "healthy": False,
                    "error": str(e),
                }

        result = {
            "status": "healthy"
            if any(p.get("healthy") for p in provider_health.values())
            else "degraded",
            "providers": provider_health,
        }

        # Include resource status if manager is available
        if self._resource_mgr:
            result["resources"] = self._resource_mgr.to_dict()

        return result
