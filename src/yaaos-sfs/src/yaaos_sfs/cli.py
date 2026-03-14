"""CLI tool for searching the YAAOS Semantic File System."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .client import DaemonClient, DaemonNotRunning
from .config import Config

console = Console()


@click.group(invoke_without_command=True)
@click.argument("query", required=False)
@click.option("--top", "-n", default=10, help="Number of results to show")
@click.option("--type", "-t", "file_type", default=None, help="Filter by extension(s), comma-separated (e.g. py,md,pdf)")
@click.option("--snippets/--no-snippets", default=True, help="Show text snippets")
@click.option("--status", is_flag=True, help="Show index status")
@click.option("--config-path", type=click.Path(), default=None, help="Config file path")
@click.pass_context
def main(ctx, query, top, file_type, snippets, status, config_path):
    """YAAOS Semantic File System — search files by meaning.

    Examples:

      yaaos-find "notes about the API redesign"

      yaaos-find "python database helpers" --type py

      yaaos-find "quarterly report" --type pdf,docx

      yaaos-find --status
    """
    from pathlib import Path

    config_p = Path(config_path) if config_path else None
    config = Config.load(config_p)

    if status:
        _show_status(config)
        return

    if not query:
        click.echo(ctx.get_help())
        return

    _do_search(config, query, top, file_type, snippets)


def _show_status(config: Config):
    # Try daemon first
    client = DaemonClient(config.query_port)
    type_breakdown = {}
    try:
        resp = client.status()
        stats = resp["stats"]
        daemon_status = "[bold green]running[/bold green]"
        provider_info = f"{resp['provider']} ({resp['model']})"
        watch_dir = resp["watch_dir"]
        type_breakdown = resp.get("type_breakdown", {})
    except DaemonNotRunning:
        from .db import Database

        db = Database(config.db_path, config.embedding_dims)
        stats = db.get_stats()
        type_breakdown = db.get_stats_by_type()
        db.close()
        daemon_status = "[bold red]not running[/bold red]"
        provider_info = f"{config.embedding_provider} ({config.embedding_model})"
        watch_dir = str(config.watch_dir)

    # Format per-type breakdown
    type_line = ""
    if type_breakdown:
        parts = [f"{count} {ext}" for ext, count in sorted(type_breakdown.items(), key=lambda x: -x[1])]
        type_line = f"\n[bold]By type:[/bold] {', '.join(parts)}"

    panel = Panel(
        f"[bold]Daemon:[/bold] {daemon_status}\n"
        f"[bold]Indexed:[/bold] {stats['files']} files | "
        f"{stats['chunks']} chunks | "
        f"DB size: {stats['db_size_mb']} MB\n"
        f"[bold]Watch dir:[/bold] {watch_dir}\n"
        f"[bold]Provider:[/bold] {provider_info}\n"
        f"[bold]Database:[/bold] {config.db_path}{type_line}",
        title="YAAOS SFS Status",
        border_style="blue",
    )
    console.print(panel)


def _do_search(config: Config, query: str, top_k: int, file_type: str | None, show_snippets: bool):
    # Try daemon first (instant — no model loading)
    client = DaemonClient(config.query_port)
    try:
        results = client.search(query, top_k=top_k)
        source = "daemon"
    except DaemonNotRunning:
        # Fallback: load model directly (slow first query)
        console.print("[dim]Daemon not running, loading model locally...[/dim]")
        from .db import Database
        from .search import hybrid_search

        # Use the same provider factory as the daemon
        from .daemon import _get_provider
        provider = _get_provider(config)

        db = Database(config.db_path, embedding_dims=provider.dims)
        results = hybrid_search(db, provider, query, top_k=top_k)
        db.close()
        source = "local"

    # Filter by type if specified (supports comma-separated: py,md,pdf)
    if file_type:
        exts = set()
        for t in file_type.split(","):
            t = t.strip()
            if t:
                exts.add(f".{t}" if not t.startswith(".") else t)
        if exts:
            results = [r for r in results if any(r.file_path.endswith(ext) for ext in exts)]

    if not results:
        console.print(f'[yellow]No results found for:[/yellow] "{query}"')
        return

    console.print(f'\n[bold blue]Results for:[/bold blue] "{query}"\n')

    for i, result in enumerate(results, 1):
        header = Text()
        header.append(f"  {i}. ", style="bold white")
        header.append(result.filename, style="bold cyan")
        header.append(f"  ({result.file_path})", style="dim")
        header.append(f"  score: {result.score:.4f}", style="green")
        console.print(header)

        if show_snippets:
            snippet = result.snippet(300)
            console.print(f"     [dim]{snippet}[/dim]")
        console.print()

    if source == "daemon":
        console.print("[dim]via daemon (instant)[/dim]")


if __name__ == "__main__":
    main()
