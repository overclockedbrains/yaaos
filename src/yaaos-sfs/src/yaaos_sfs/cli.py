"""CLI tool for searching the YAAOS Semantic File System."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .config import Config
from .db import Database
from .providers.local import LocalEmbeddingProvider
from .search import hybrid_search


console = Console()


def _get_provider(config: Config):
    if config.embedding_provider == "openai":
        from .providers.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(config.openai_api_key, config.openai_model)
    return LocalEmbeddingProvider(config.embedding_model)


@click.group(invoke_without_command=True)
@click.argument("query", required=False)
@click.option("--top", "-n", default=10, help="Number of results to show")
@click.option("--type", "-t", "file_type", default=None, help="Filter by extension (e.g. py, md)")
@click.option("--snippets/--no-snippets", default=True, help="Show text snippets")
@click.option("--status", is_flag=True, help="Show index status")
@click.option("--config-path", type=click.Path(), default=None, help="Config file path")
@click.pass_context
def main(ctx, query, top, file_type, snippets, status, config_path):
    """YAAOS Semantic File System — search files by meaning.

    Examples:

      yaaos-find "notes about the API redesign"

      yaaos-find "python database helpers" --type py

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
    db = Database(config.db_path, config.embedding_dims)
    stats = db.get_stats()
    db.close()

    panel = Panel(
        f"[bold]Indexed:[/bold] {stats['files']} files | "
        f"{stats['chunks']} chunks | "
        f"DB size: {stats['db_size_mb']} MB\n"
        f"[bold]Watch dir:[/bold] {config.watch_dir}\n"
        f"[bold]Provider:[/bold] {config.embedding_provider} ({config.embedding_model})\n"
        f"[bold]Database:[/bold] {config.db_path}",
        title="YAAOS SFS Status",
        border_style="blue",
    )
    console.print(panel)


def _do_search(config: Config, query: str, top_k: int, file_type: str | None, show_snippets: bool):
    provider = _get_provider(config)
    db = Database(config.db_path, embedding_dims=provider.dims)

    results = hybrid_search(db, provider, query, top_k=top_k)
    db.close()

    # Filter by type if specified
    if file_type:
        ext = f".{file_type}" if not file_type.startswith(".") else file_type
        results = [r for r in results if r.file_path.endswith(ext)]

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


if __name__ == "__main__":
    main()
