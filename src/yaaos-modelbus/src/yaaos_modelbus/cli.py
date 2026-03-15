"""CLI for the Model Bus — yaaos-bus command.

Provides health checks, model listing, quick embed/generate tests,
and config management.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from yaaos_modelbus.client import AsyncModelBusClient, ModelBusClient
from yaaos_modelbus.errors import DaemonNotRunning

console = Console()


@dataclass
class CliContext:
    """Shared state passed through Click's context system."""

    socket_path: str | None = None

    def client(self) -> ModelBusClient:
        return ModelBusClient(self.socket_path)

    def async_client(self) -> AsyncModelBusClient:
        return AsyncModelBusClient(self.socket_path)


pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


@click.group()
@click.option(
    "--socket",
    "-s",
    "socket_path",
    default=None,
    envvar="YAAOS_MODELBUS_SOCKET",
    help="Override socket path (default: auto-discover, or set YAAOS_MODELBUS_SOCKET)",
)
@click.pass_context
def main(ctx: click.Context, socket_path: str | None):
    """YAAOS Model Bus CLI — manage the unified AI inference daemon."""
    from dotenv import load_dotenv

    load_dotenv()  # Load .env so YAAOS_MODELBUS_SOCKET and API keys are available

    # If no --socket flag was passed, re-check env after dotenv load
    if socket_path is None:
        import os

        socket_path = os.environ.get("YAAOS_MODELBUS_SOCKET")

    ctx.ensure_object(CliContext)
    ctx.obj.socket_path = socket_path


@main.command()
@pass_ctx
def health(ctx: CliContext):
    """Check Model Bus daemon health."""
    client = ctx.client()
    try:
        result = client.health()
    except DaemonNotRunning as e:
        console.print(f"[red]Model Bus not running:[/red] {e}")
        sys.exit(1)

    status = result.get("status", "unknown")
    color = "green" if status == "healthy" else "yellow"

    lines = [f"[{color}]Status: {status}[/{color}]"]

    # Resources
    resources = result.get("resources", {})
    gpu = resources.get("gpu")
    if gpu:
        lines.append(
            f"GPU: {gpu['name']} — {gpu.get('vram_free_mb', '?')} MB / "
            f"{gpu.get('vram_total_mb', '?')} MB free"
        )
    ram = resources.get("ram", {})
    if ram:
        lines.append(
            f"RAM: {ram.get('available_mb', '?')} MB / {ram.get('total_mb', '?')} MB available"
        )

    # Providers
    providers = result.get("providers", {})
    if providers:
        lines.append("")
        lines.append("[bold]Providers:[/bold]")
        for name, prov in providers.items():
            healthy = prov.get("healthy", False)
            icon = "[green]✓[/green]" if healthy else "[red]✗[/red]"
            latency = prov.get("latency_ms")
            lat_str = f" ({latency:.0f}ms)" if latency else ""
            error = prov.get("error")
            err_str = f" — [red]{error}[/red]" if error else ""
            models = prov.get("models_loaded", [])
            model_str = f"  models: {', '.join(models)}" if models else ""
            lines.append(f"  {icon} {name}{lat_str}{err_str}{model_str}")

    # Loaded models
    loaded = result.get("models_loaded", [])
    if loaded:
        lines.append("")
        lines.append("[bold]Loaded models:[/bold]")
        for m in loaded:
            idle = m.get("idle_sec", 0)
            vram = m.get("vram_mb", "?")
            lines.append(f"  {m['id']} ({vram} MB VRAM, idle {idle}s)")

    console.print(Panel("\n".join(lines), title="Model Bus Health", border_style="blue"))


@main.command("models")
@pass_ctx
def list_models(ctx: CliContext):
    """List available models across all providers."""
    client = ctx.client()
    try:
        models = client.list_models()
    except DaemonNotRunning as e:
        console.print(f"[red]Model Bus not running:[/red] {e}")
        sys.exit(1)

    if not models:
        console.print("[yellow]No models available[/yellow]")
        return

    table = Table(title="Available Models")
    table.add_column("Model", style="cyan")
    table.add_column("Params", justify="right")
    table.add_column("Quant")
    table.add_column("Capabilities")
    table.add_column("Details")

    for m in models:
        params = f"{m.get('params_billions', '')}B" if m.get("params_billions") else "—"
        quant = m.get("quantization", "—") or "—"
        caps = ", ".join(m.get("capabilities", []))

        details = []
        if m.get("estimated_vram_mb"):
            details.append(f"~{m['estimated_vram_mb']} MB VRAM")
        if m.get("embedding_dims"):
            details.append(f"{m['embedding_dims']} dims")
        if m.get("context_length"):
            details.append(f"{m['context_length']} ctx")

        table.add_row(m["id"], params, quant, caps, ", ".join(details) or "cloud")

    console.print(table)


@main.command()
@click.argument("text")
@click.option("--model", "-m", default=None, help="Model to use (e.g. ollama/nomic-embed-text)")
@pass_ctx
def embed(ctx: CliContext, text: str, model: str | None):
    """Embed text and show the result."""
    client = ctx.client()
    try:
        result = client.embed([text], model=model)
    except DaemonNotRunning as e:
        console.print(f"[red]Model Bus not running:[/red] {e}")
        sys.exit(1)

    dims = result.get("dims", 0)
    model_used = result.get("model", "?")
    embeddings = result.get("embeddings", [])

    console.print(f"Model: [cyan]{model_used}[/cyan] | Dims: {dims}")
    if embeddings:
        vec = embeddings[0]
        preview = ", ".join(f"{v:.4f}" for v in vec[:8])
        console.print(f"[{preview}, ...]")


@main.command()
@click.argument("prompt")
@click.option("--model", "-m", default=None, help="Model to use (e.g. ollama/phi3:mini)")
@click.option("--system", "-s", default=None, help="System prompt")
@pass_ctx
def generate(ctx: CliContext, prompt: str, model: str | None, system: str | None):
    """Generate text from a prompt (streaming)."""

    async def _stream():
        client = ctx.async_client()
        try:
            async for chunk in client.generate(prompt, model=model, system=system, stream=True):
                if chunk.get("done"):
                    console.print()  # newline after streaming
                    usage = chunk.get("usage")
                    if usage:
                        console.print(
                            f"[dim]({usage.get('prompt_tokens', '?')} prompt, "
                            f"{usage.get('completion_tokens', '?')} completion tokens)[/dim]"
                        )
                elif "token" in chunk:
                    console.print(chunk["token"], end="")
        except DaemonNotRunning as e:
            console.print(f"[red]Model Bus not running:[/red] {e}")
            sys.exit(1)

    asyncio.run(_stream())


@main.group("config")
def config_group():
    """View and modify Model Bus configuration."""


@config_group.command("get")
@click.argument("key", required=False)
def config_get(key: str | None):
    """Get a config value (or show full config).

    Keys: defaults.embedding, defaults.generation, defaults.chat,
    daemon.socket_path, daemon.log_level, daemon.max_concurrent_requests,
    resources.max_vram_usage_pct, resources.model_idle_timeout_sec
    """
    from yaaos_modelbus.config import Config

    config = Config.load()

    # Build a flat key→value map for display
    flat = _config_to_flat(config)

    if key:
        if key in flat:
            console.print(f"[cyan]{key}[/cyan] = {flat[key]}")
        else:
            console.print(f"[red]Unknown key:[/red] {key}")
            console.print(f"Available: {', '.join(sorted(flat.keys()))}")
            sys.exit(1)
    else:
        table = Table(title="Model Bus Configuration")
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        for k in sorted(flat.keys()):
            table.add_row(k, str(flat[k]))
        console.print(table)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a config value and write to TOML.

    Example: yaaos-bus config set defaults.generation openai/gpt-4o
    """
    from pathlib import Path

    from yaaos_modelbus.config import Config, _DEFAULT_CONFIG_PATH

    config = Config.load()
    config_path = Path(_DEFAULT_CONFIG_PATH).expanduser()

    flat = _config_to_flat(config)
    if key not in flat:
        console.print(f"[red]Unknown key:[/red] {key}")
        console.print(f"Available: {', '.join(sorted(flat.keys()))}")
        sys.exit(1)

    _apply_config_value(config, key, value)

    # Write back to TOML
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_config_toml(config, config_path)

    console.print(f"[green]Set[/green] [cyan]{key}[/cyan] = {value}")
    console.print(f"Config written to {config_path}")


def _config_to_flat(config) -> dict[str, str]:
    """Convert a Config to a flat key→value dict."""
    flat = {
        "defaults.embedding": config.default_embedding,
        "defaults.generation": config.default_generation,
        "defaults.chat": config.default_chat,
        "daemon.socket_path": str(config.socket_path),
        "daemon.log_level": config.log_level,
        "daemon.max_concurrent_requests": str(config.max_concurrent_requests),
        "resources.max_vram_usage_pct": str(config.resources.max_vram_usage_pct),
        "resources.model_idle_timeout_sec": str(config.resources.model_idle_timeout_sec),
        "resources.max_ram_usage_pct": str(config.resources.max_ram_usage_pct),
    }
    # Add provider entries
    for name, prov in config.providers.items():
        flat[f"providers.{name}.enabled"] = str(prov.enabled).lower()
        if prov.base_url:
            flat[f"providers.{name}.base_url"] = prov.base_url
    return flat


def _apply_config_value(config, key: str, value: str) -> None:
    """Apply a flat key=value to a Config object."""
    from pathlib import Path

    if key == "defaults.embedding":
        config.default_embedding = value
    elif key == "defaults.generation":
        config.default_generation = value
    elif key == "defaults.chat":
        config.default_chat = value
    elif key == "daemon.socket_path":
        config.socket_path = Path(value)
    elif key == "daemon.log_level":
        config.log_level = value
    elif key == "daemon.max_concurrent_requests":
        config.max_concurrent_requests = int(value)
    elif key == "resources.max_vram_usage_pct":
        config.resources.max_vram_usage_pct = int(value)
    elif key == "resources.model_idle_timeout_sec":
        config.resources.model_idle_timeout_sec = int(value)
    elif key == "resources.max_ram_usage_pct":
        config.resources.max_ram_usage_pct = int(value)
    elif key.startswith("providers.") and key.endswith(".enabled"):
        prov_name = key.split(".")[1]
        if prov_name in config.providers:
            config.providers[prov_name].enabled = value.lower() in ("true", "1", "yes")
    elif key.startswith("providers.") and key.endswith(".base_url"):
        prov_name = key.split(".")[1]
        if prov_name in config.providers:
            config.providers[prov_name].base_url = value


def _write_config_toml(config, path) -> None:
    """Write a Config object to TOML format."""
    lines = [
        "# YAAOS Model Bus configuration",
        "# Auto-generated by yaaos-bus config set",
        "",
        "[daemon]",
        f'socket_path = "{config.socket_path}"',
        f'log_level = "{config.log_level}"',
        f"max_concurrent_requests = {config.max_concurrent_requests}",
        "",
        "[defaults]",
        f'embedding = "{config.default_embedding}"',
        f'generation = "{config.default_generation}"',
        f'chat = "{config.default_chat}"',
        "",
        "[resources]",
        f"max_vram_usage_pct = {config.resources.max_vram_usage_pct}",
        f"model_idle_timeout_sec = {config.resources.model_idle_timeout_sec}",
        f"max_ram_usage_pct = {config.resources.max_ram_usage_pct}",
    ]

    for name, prov in config.providers.items():
        lines.append("")
        lines.append(f"[providers.{name}]")
        lines.append(f"enabled = {'true' if prov.enabled else 'false'}")
        if prov.base_url:
            lines.append(f'base_url = "{prov.base_url}"')

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
