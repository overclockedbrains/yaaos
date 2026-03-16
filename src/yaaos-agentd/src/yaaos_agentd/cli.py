"""CLI for SystemAgentd — systemagentctl command.

Provides agent management, health checks, tool listing, and invocation.
Communicates with the daemon over the Agent Bus Unix socket.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from yaaos_agentd.client import AgentBusClient, AsyncAgentBusClient
from yaaos_agentd.errors import DaemonNotRunning

console = Console()


@dataclass
class CliContext:
    """Shared state passed through Click's context system."""

    socket_path: str | None = None

    def client(self) -> AgentBusClient:
        return AgentBusClient(self.socket_path)

    def async_client(self) -> AsyncAgentBusClient:
        return AsyncAgentBusClient(self.socket_path)


pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


@click.group()
@click.option(
    "--socket",
    "-s",
    "socket_path",
    default=None,
    envvar="YAAOS_AGENTBUS_SOCKET",
    help="Override socket path (default: auto-discover, or set YAAOS_AGENTBUS_SOCKET)",
)
@click.pass_context
def main(ctx: click.Context, socket_path: str | None):
    """YAAOS SystemAgentd CLI — manage agents and tools."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    if socket_path is None:
        import os

        socket_path = os.environ.get("YAAOS_AGENTBUS_SOCKET")

    ctx.ensure_object(CliContext)
    ctx.obj.socket_path = socket_path


# ── Status Commands ──────────────────────────────────────────


@main.command()
@pass_ctx
def status(ctx: CliContext):
    """Show supervisor and agent status overview."""
    client = ctx.client()
    try:
        health = client.health()
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)

    sup_status = health.get("status", "unknown")
    color = "green" if sup_status == "healthy" else "yellow" if sup_status == "degraded" else "red"
    uptime = _format_duration(health.get("uptime_sec", 0))

    agent_count = health.get("agent_count", 0)
    running = health.get("agents_running", 0)

    # Process metrics line
    proc_parts: list[str] = []
    if "pid" in health:
        proc_parts.append(f"PID {health['pid']}")
    if "process_memory_mb" in health:
        proc_parts.append(f"Mem {health['process_memory_mb']} MB")
    if "process_cpu_percent" in health:
        proc_parts.append(f"CPU {health['process_cpu_percent']}%")

    lines = [
        f"[{color}]SystemAgentd: {sup_status}[/{color}] (uptime {uptime})",
        f"Agents: {running}/{agent_count} running",
    ]
    if proc_parts:
        lines.append(f"Process: {' | '.join(proc_parts)}")

    if health.get("agents_failed", 0) > 0:
        lines.append(f"[red]Failed: {health['agents_failed']}[/red]")
    if health.get("agents_degraded", 0) > 0:
        lines.append(f"[yellow]Degraded: {health['agents_degraded']}[/yellow]")

    console.print(Panel("\n".join(lines), title="SystemAgentd Health", border_style="blue"))

    # Agent table
    try:
        agents = client.list_agents()
    except DaemonNotRunning:
        return

    if agents:
        table = Table()
        table.add_column("Agent", style="cyan")
        table.add_column("State")
        table.add_column("Cycles", justify="right")
        table.add_column("Last Cycle", justify="right")
        table.add_column("Errors", justify="right")

        for a in agents:
            state = a.get("state", "unknown")
            state_color = _state_color(state)
            icon = _state_icon(state)

            last_cycle = ""
            if "last_cycle_ago_sec" in a:
                last_cycle = f"{a['last_cycle_ago_sec']:.0f}s ago"

            errors = str(a.get("error_count", 0))
            if a.get("error_count", 0) > 0:
                errors = f"[red]{errors}[/red]"

            table.add_row(
                a["name"],
                f"{icon} [{state_color}]{state}[/{state_color}]",
                str(a.get("cycle_count", 0)),
                last_cycle,
                errors,
            )

        console.print(table)


@main.command("agent")
@click.argument("name")
@pass_ctx
def agent_detail(ctx: CliContext, name: str):
    """Show detailed status for a specific agent."""
    client = ctx.client()
    try:
        status = client.agent_status(name)
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    state = status.get("state", "unknown")
    state_color = _state_color(state)

    lines = [
        f"[bold]Agent:[/bold] {status['name']}",
        f"[bold]State:[/bold] [{state_color}]{state}[/{state_color}]",
    ]

    if "pid" in status:
        lines.append(f"[bold]PID:[/bold] {status['pid']}")
    if "uptime_sec" in status:
        lines.append(f"[bold]Uptime:[/bold] {_format_duration(status['uptime_sec'])}")
    lines.append(f"[bold]Cycles:[/bold] {status.get('cycle_count', 0)}")
    lines.append(f"[bold]Errors:[/bold] {status.get('error_count', 0)}")

    if "last_cycle_ago_sec" in status:
        lines.append(f"[bold]Last cycle:[/bold] {status['last_cycle_ago_sec']:.0f}s ago")
    if status.get("last_action"):
        lines.append(f"[bold]Last action:[/bold] {status['last_action']}")
    if status.get("last_error"):
        lines.append(f"[bold]Last error:[/bold] [red]{status['last_error']}[/red]")
    if "memory_mb" in status:
        lines.append(f"[bold]Memory:[/bold] {status['memory_mb']} MB")
    if "cpu_percent" in status:
        lines.append(f"[bold]CPU:[/bold] {status['cpu_percent']}%")

    console.print(Panel("\n".join(lines), title=f"Agent: {name}", border_style="cyan"))


# ── Agent Control Commands ───────────────────────────────────


@main.command("start")
@click.argument("name")
@pass_ctx
def start_agent(ctx: CliContext, name: str):
    """Start a stopped or failed agent."""
    client = ctx.client()
    try:
        client.start_agent(name)
        console.print(f"[green]Started agent:[/green] {name}")
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command("stop")
@click.argument("name")
@pass_ctx
def stop_agent(ctx: CliContext, name: str):
    """Stop a running agent."""
    client = ctx.client()
    try:
        client.stop_agent(name)
        console.print(f"[yellow]Stopped agent:[/yellow] {name}")
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command("restart")
@click.argument("name")
@pass_ctx
def restart_agent(ctx: CliContext, name: str):
    """Restart an agent (resets crash loop limiter)."""
    client = ctx.client()
    try:
        client.restart_agent(name)
        console.print(f"[green]Restarted agent:[/green] {name}")
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# ── Agent Logs Command ───────────────────────────────────────


@main.command("logs")
@click.argument("name")
@click.option("--lines", "-n", default=50, help="Number of recent log lines to show")
@pass_ctx
def agent_logs(ctx: CliContext, name: str, lines: int):
    """Show recent journal log entries for an agent."""
    client = ctx.client()
    try:
        result = client.agent_logs(name, lines=lines)
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if result.get("error"):
        console.print(f"[yellow]Warning:[/yellow] {result['error']}")

    log_lines = result.get("lines", [])
    if not log_lines:
        console.print(f"[dim]No log entries found for agent {name}[/dim]")
        return

    console.print(f"[bold]Logs for agent:[/bold] {name} (unit: {result.get('unit', 'unknown')})\n")
    for line in log_lines:
        console.print(line)


# ── Tools Commands ───────────────────────────────────────────


@main.group("tools")
def tools_group():
    """Manage and invoke registered tools."""


@tools_group.command("list")
@pass_ctx
def tools_list(ctx: CliContext):
    """List all registered tools."""
    client = ctx.client()
    try:
        tools = client.list_tools()
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)

    if not tools:
        console.print("[yellow]No tools registered[/yellow]")
        return

    table = Table(title="Registered Tools")
    table.add_column("Tool", style="cyan")
    table.add_column("Description")
    table.add_column("Actions", justify="right")
    table.add_column("Binary")

    for t in tools:
        actions = ", ".join(t.get("actions", []))
        table.add_row(
            t["name"],
            t.get("description", ""),
            actions,
            t.get("binary", ""),
        )

    console.print(table)


@tools_group.command("schema")
@click.argument("tool_name")
@pass_ctx
def tools_schema(ctx: CliContext, tool_name: str):
    """Show JSON Schema for a tool's actions."""
    client = ctx.client()
    try:
        result = client.tool_schema(tool_name)
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    import json

    console.print(f"[bold]Tool:[/bold] {result.get('tool', tool_name)}")
    for name, schema in result.get("schemas", {}).items():
        console.print(f"\n[cyan]{name}[/cyan]: {schema.get('description', '')}")
        if schema.get("parameters"):
            console.print(json.dumps(schema["parameters"], indent=2))


@tools_group.command("invoke", context_settings={"ignore_unknown_options": True})
@click.argument("tool_name")
@click.argument("action")
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.option("--param", "-p", multiple=True, help="Parameters as key=value")
@click.option("--timeout", "-t", default=30.0, help="Timeout in seconds")
@pass_ctx
def tools_invoke(
    ctx: CliContext, tool_name: str, action: str, extra_args: tuple, param: tuple, timeout: float
):
    """Invoke a tool action manually.

    Pass parameters as key=value after the action, or use -p key=value.

    \b
    Examples:
        systemagentctl tools invoke docker logs container_id=50f
        systemagentctl tools invoke docker ps
        systemagentctl tools invoke docker logs -p container_id=50f
    """
    params = {}
    # Merge -p flags and positional key=value args
    for p in (*param, *extra_args):
        if "=" in p:
            key, value = p.split("=", 1)
            params[key] = value
        else:
            console.print(f"[red]Invalid param format:[/red] {p} (expected key=value)")
            sys.exit(1)

    client = ctx.client()
    try:
        result = client.invoke_tool(tool_name, action, params, timeout=timeout)
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if result.get("is_error"):
        console.print(f"[red]Exit code {result.get('exit_code')}[/red]")
        if result.get("stderr"):
            console.print(result["stderr"])
    else:
        if result.get("stdout"):
            console.print(result["stdout"], end="")

    if result.get("duration_ms"):
        console.print(f"\n[dim]({result['duration_ms']:.0f}ms)[/dim]")


# ── Config Commands ──────────────────────────────────────────


@main.command("reload")
@pass_ctx
def reload_config(ctx: CliContext):
    """Hot-reload agent configuration."""
    client = ctx.client()
    try:
        result = client.reload_config()
        agents = result.get("agents", [])
        console.print(f"[green]Config reloaded.[/green] Agents: {', '.join(agents)}")
    except DaemonNotRunning as e:
        console.print(f"[red]SystemAgentd not running:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def _state_color(state: str) -> str:
    """Get Rich color for an agent state."""
    return {
        "running": "green",
        "starting": "blue",
        "degraded": "yellow",
        "stopped": "dim",
        "failed": "red",
        "crash_loop": "red bold",
        "spec_only": "dim",
        "stopping": "yellow",
    }.get(state, "white")


def _state_icon(state: str) -> str:
    """Get status icon for an agent state."""
    return {
        "running": "[green]●[/green]",
        "starting": "[blue]◐[/blue]",
        "degraded": "[yellow]◑[/yellow]",
        "stopped": "[dim]○[/dim]",
        "failed": "[red]✗[/red]",
        "crash_loop": "[red]⟳[/red]",
        "spec_only": "[dim]·[/dim]",
        "stopping": "[yellow]◌[/yellow]",
    }.get(state, "?")


if __name__ == "__main__":
    main()
