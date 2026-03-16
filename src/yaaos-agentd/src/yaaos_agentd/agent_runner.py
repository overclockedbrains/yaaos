"""Single-agent process runner.

Runs a single agent as a standalone process, suitable for systemd
template units (systemagentd-agent@.service). Handles:
- Agent class loading via entry points or module import
- sd_notify integration (READY=1, WATCHDOG=1, STOPPING=1)
- Signal handling (SIGTERM, SIGINT, SIGHUP)
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import importlib
import os
import signal
import sys

import structlog

from yaaos_agentd.config import Config
from yaaos_agentd.types import AgentSpec

logger = structlog.get_logger()


def _configure_logging(level: str) -> None:
    """Set up structlog with JSON output (matches Model Bus pattern)."""
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


def load_agent_class(spec: AgentSpec):
    """Load an agent class from its module path.

    The module should contain a class whose name ends with 'Agent'
    (e.g., LogAgent, CrashAgent) or matches the conventional name.
    """
    module = importlib.import_module(spec.module)

    # Convention: class name is CamelCase of agent name + "Agent"
    # e.g., "log" → "LogAgent", "crash" → "CrashAgent"
    expected_name = spec.name.replace("-", " ").title().replace(" ", "") + "Agent"

    # Try exact match first
    if hasattr(module, expected_name):
        return getattr(module, expected_name)

    # Fall back to finding any BaseAgent subclass
    from yaaos_agentd.agent_base import BaseAgent

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseAgent)
            and attr is not BaseAgent
        ):
            return attr

    raise ImportError(
        f"No agent class found in {spec.module}. "
        f"Expected '{expected_name}' or a BaseAgent subclass."
    )


def _create_model_bus_client():
    """Try to create a Model Bus async client. Returns None if unavailable."""
    try:
        from yaaos_modelbus.client import AsyncModelBusClient
        return AsyncModelBusClient()
    except Exception:
        logger.info("agent_runner.model_bus_unavailable")
        return None


async def run_agent(spec: AgentSpec, config: Config) -> None:
    """Run a single agent with signal handling and sd_notify."""
    log = logger.bind(agent=spec.name)
    log.info("agent_runner.starting", module=spec.module)

    # Load agent class
    agent_cls = load_agent_class(spec)
    log.info("agent_runner.loaded_class", cls=agent_cls.__name__)

    # Create agent instance with available service clients
    model_bus_client = _create_model_bus_client()
    agent = agent_cls(spec, model_bus_client=model_bus_client)

    # Set up signal handling
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    reload_event = asyncio.Event()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown.set)
        loop.add_signal_handler(signal.SIGHUP, reload_event.set)
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f: shutdown.set())

    # Start the agent loop as a task
    agent_task = asyncio.create_task(agent.run_loop())

    # sd_notify: READY=1
    _sd_notify("READY=1")
    _sd_notify(f"STATUS=Agent {spec.name} running")

    # Watchdog loop
    watchdog_task = _start_watchdog()

    # Wait for shutdown or reload signals
    try:
        while not shutdown.is_set():
            reload_t = asyncio.create_task(reload_event.wait())
            shutdown_t = asyncio.create_task(shutdown.wait())
            done, pending = await asyncio.wait(
                [reload_t, shutdown_t, agent_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            # Agent task completed on its own (crash or clean exit)
            if agent_task in done:
                break

            # Reload requested
            if reload_event.is_set() and not shutdown.is_set():
                reload_event.clear()
                log.info("agent_runner.reloading_config")
                new_config = Config.load()
                new_spec = new_config.agents.get(spec.name)
                if new_spec:
                    await agent.on_reload(new_spec.config)
                    log.info("agent_runner.config_reloaded")
    finally:
        # Graceful shutdown
        _sd_notify("STOPPING=1")
        log.info("agent_runner.stopping")

        if watchdog_task:
            watchdog_task.cancel()

        agent.request_stop()
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass

        log.info("agent_runner.stopped")


def _running_under_systemd() -> bool:
    """Check if we're running as a systemd Type=notify service."""
    return bool(os.environ.get("NOTIFY_SOCKET"))


def _sd_notify(state: str) -> None:
    """Send sd_notify message.

    When running under systemd (NOTIFY_SOCKET set), failing to notify
    is a real problem — systemd will consider the service timed-out.
    Warn loudly if sdnotify is missing in that case.
    """
    try:
        import sdnotify

        sdnotify.SystemdNotifier().notify(state)
    except ImportError:
        if _running_under_systemd():
            logger.warning(
                "agent_runner.sdnotify_missing",
                state=state,
                hint="Install sdnotify: pip install sdnotify. "
                "Without it, systemd Type=notify will timeout.",
            )


def _start_watchdog() -> asyncio.Task | None:
    """Start watchdog ping loop if WatchdogSec is configured."""
    watchdog_usec = int(os.environ.get("WATCHDOG_USEC", 0))
    if watchdog_usec <= 0:
        return None

    interval = watchdog_usec / 1_000_000 / 2  # Ping at half the interval

    async def _loop():
        while True:
            _sd_notify("WATCHDOG=1")
            await asyncio.sleep(interval)

    return asyncio.create_task(_loop())


def main() -> None:
    """CLI entry point for yaaos-agent."""
    from dotenv import load_dotenv

    load_dotenv()

    import click

    @click.command()
    @click.argument("agent_name")
    @click.option(
        "--config",
        "-c",
        "config_path",
        type=click.Path(exists=False),
        default=None,
        help="Path to config file",
    )
    def _main(agent_name: str, config_path: str | None):
        """Run a single YAAOS agent."""
        from pathlib import Path

        config = Config.load(Path(config_path) if config_path else None)
        _configure_logging(config.supervisor.log_level)

        spec = config.agents.get(agent_name)
        if spec is None:
            # Create a minimal spec for the agent
            spec = AgentSpec(
                name=agent_name,
                module=f"yaaos_agentd.agents.{agent_name}_agent",
            )

        try:
            asyncio.run(run_agent(spec, config))
        except KeyboardInterrupt:
            pass

    _main()


if __name__ == "__main__":
    main()
