"""Model Bus daemon — main entry point.

Starts the JSON-RPC server on a Unix socket, initializes providers,
and handles signals for graceful shutdown.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog

from yaaos_modelbus.config import Config
from yaaos_modelbus.resources import ResourceManager
from yaaos_modelbus.router import Router
from yaaos_modelbus.server import JsonRpcServer

logger = structlog.get_logger()


def _configure_logging(level: str) -> None:
    """Set up structlog with JSON output."""
    import logging

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


async def _init_providers(config: Config) -> dict:
    """Initialize enabled providers from config.

    Returns a dict of provider_name → provider_instance.
    Providers that fail to initialize are logged and skipped.
    """
    registry = {}

    for name, prov_config in config.providers.items():
        if not prov_config.enabled:
            logger.info("provider.disabled", provider=name)
            continue

        try:
            provider = await _create_provider(name, prov_config)
            if provider:
                registry[name] = provider
                logger.info("provider.initialized", provider=name)
        except Exception as e:
            logger.warning("provider.init_failed", provider=name, error=str(e))

    return registry


async def _create_provider(name: str, prov_config):
    """Create a single provider instance by name."""
    if name == "ollama":
        from yaaos_modelbus.providers.ollama import OllamaProvider

        base_url = prov_config.base_url or "http://localhost:11434"
        return OllamaProvider(base_url=base_url)

    elif name == "openai":
        try:
            from yaaos_modelbus.providers.openai import OpenAIProvider

            api_key = prov_config.api_key
            if not api_key:
                logger.info("provider.no_api_key", provider="openai")
                return None
            return OpenAIProvider(api_key=api_key)
        except ImportError:
            logger.info("provider.not_installed", provider="openai", package="openai")
            return None

    elif name == "anthropic":
        try:
            from yaaos_modelbus.providers.anthropic import AnthropicProvider

            api_key = prov_config.api_key
            if not api_key:
                logger.info("provider.no_api_key", provider="anthropic")
                return None
            return AnthropicProvider(api_key=api_key)
        except ImportError:
            logger.info("provider.not_installed", provider="anthropic", package="anthropic")
            return None

    elif name == "voyage":
        try:
            from yaaos_modelbus.providers.voyage import VoyageProvider

            api_key = prov_config.api_key
            if not api_key:
                logger.info("provider.no_api_key", provider="voyage")
                return None
            return VoyageProvider(api_key=api_key)
        except ImportError:
            logger.info("provider.not_installed", provider="voyage", package="voyageai")
            return None

    elif name == "local":
        try:
            from yaaos_modelbus.providers.local import LocalProvider

            device = prov_config.extra.get("device")
            return LocalProvider(device=device)
        except ImportError:
            logger.info(
                "provider.not_installed",
                provider="local",
                package="sentence-transformers",
            )
            return None

    else:
        logger.warning("provider.unknown", provider=name)
        return None


async def run_daemon(config: Config) -> None:
    """Run the Model Bus daemon."""
    _configure_logging(config.log_level)
    logger.info(
        "daemon.starting",
        socket=str(config.socket_path),
        version="0.1.0",
    )

    # Initialize providers
    registry = await _init_providers(config)
    if not registry:
        logger.error("daemon.no_providers", msg="No providers initialized — exiting")
        sys.exit(1)

    # Initialize resource manager
    resource_mgr = ResourceManager(config.resources)
    status = resource_mgr.get_status()
    logger.info(
        "daemon.resources",
        gpu=status.gpu_name,
        vram_total_mb=status.vram_total_mb,
        ram_total_mb=status.ram_total_mb,
    )

    # Model unload callback for eviction and capacity management
    async def _unload_model(model_id: str, provider_name: str) -> None:
        """Unload a model via Ollama API (or no-op for cloud providers)."""
        if provider_name == "ollama" and "ollama" in registry:
            prov = registry["ollama"]
            model_name = model_id.split("/", 1)[-1] if "/" in model_id else model_id
            try:
                await prov._client.post(
                    "/api/generate",
                    json={"model": model_name, "keep_alive": 0},
                    timeout=10.0,
                )
                logger.info("daemon.model_unloaded", model=model_id)
            except Exception as e:
                logger.warning("daemon.unload_failed", model=model_id, error=str(e))

    # Create router with resource manager and capacity callback
    router = Router(config, registry, resource_manager=resource_mgr, unload_callback=_unload_model)

    # Create server and register handlers
    server = JsonRpcServer(
        socket_path=config.socket_path,
        max_connections=config.max_concurrent_requests,
    )

    # Hot-reload handler — re-reads config and reinitializes providers
    async def handle_config_reload(params: dict) -> dict:
        nonlocal config, registry
        config_path = params.get("config_path")
        new_config = Config.load(Path(config_path) if config_path else None)

        # Build new registry FIRST — old providers stay active during init
        new_registry = await _init_providers(new_config)
        if not new_registry:
            logger.error("config.reload_failed", msg="No providers after reload")
            return {"success": False, "error": "No providers initialized after reload"}

        # Atomic swap — update all references before closing old providers
        old_registry = registry
        config = new_config
        registry = new_registry
        router.config = new_config
        router.set_registry(new_registry)

        # Close OLD providers after swap (in-flight requests may still reference them briefly)
        for name, prov in old_registry.items():
            if hasattr(prov, "close"):
                try:
                    await prov.close()
                except Exception:
                    pass

        logger.info("config.reloaded", providers=list(new_registry.keys()))
        return {"success": True, "providers": list(new_registry.keys())}

    server.register("health", router.handle_health)
    server.register("embed", router.handle_embed)
    server.register("models.list", router.handle_models_list)
    server.register("config.reload", handle_config_reload)
    server.register_stream("generate", router.handle_generate)
    server.register_stream("chat", router.handle_chat)

    # Start server and eviction loop
    await server.start()
    await resource_mgr.start_eviction_loop(_unload_model)

    # Notify systemd if available
    try:
        import sdnotify

        notifier = sdnotify.SystemdNotifier()
        notifier.notify("READY=1")
        logger.info("daemon.systemd_ready")
    except ImportError:
        pass

    logger.info(
        "daemon.ready",
        providers=list(registry.keys()),
        max_connections=config.max_concurrent_requests,
    )

    # Wait for shutdown signal
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown.set)
    else:
        # Windows: add_signal_handler is not supported, use signal.signal fallback
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f: shutdown.set())

    await shutdown.wait()

    logger.info("daemon.shutting_down")

    # Notify systemd
    try:
        import sdnotify

        sdnotify.SystemdNotifier().notify("STOPPING=1")
    except ImportError:
        pass

    # Stop eviction loop, then server (which drains in-flight), then close providers
    await resource_mgr.stop_eviction_loop()
    await server.stop()

    for name, prov in registry.items():
        if hasattr(prov, "close"):
            try:
                await prov.close()
            except Exception:
                pass

    logger.info("daemon.stopped")


def main() -> None:
    """CLI entry point for yaaos-modelbusd."""
    from dotenv import load_dotenv

    load_dotenv()  # Load .env before anything reads os.environ

    import click

    @click.command()
    @click.option(
        "--config",
        "-c",
        "config_path",
        type=click.Path(exists=False),
        default=None,
        help="Path to config file (default: ~/.config/yaaos/modelbus.toml)",
    )
    @click.option(
        "--socket",
        "-s",
        "socket_path",
        type=click.Path(),
        default=None,
        envvar="YAAOS_MODELBUS_SOCKET",
        help="Override socket path (or set YAAOS_MODELBUS_SOCKET)",
    )
    def _main(config_path: str | None, socket_path: str | None):
        """Start the YAAOS Model Bus daemon."""
        config = Config.load(Path(config_path) if config_path else None)

        if socket_path:
            config.socket_path = Path(socket_path)

        try:
            asyncio.run(run_daemon(config))
        except KeyboardInterrupt:
            pass

    _main()


if __name__ == "__main__":
    main()
