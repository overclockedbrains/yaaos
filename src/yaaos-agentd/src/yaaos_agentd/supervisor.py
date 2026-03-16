"""OTP-style supervisor with Kubernetes reconciliation loop.

SystemAgentd's core: manages agent lifecycle with fault tolerance.
Combines Erlang/OTP restart strategies with Kubernetes level-triggered
reconciliation to provide self-healing agent management.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.agent_runner import _configure_logging, load_agent_class
from yaaos_agentd.config import Config
from yaaos_agentd.types import (
    AgentSpec,
    AgentState,
    AgentStatus,
    RestartPolicy,
    RestartStrategy,
    SupervisorHealth,
)

logger = structlog.get_logger()


# ── Process Metrics ──────────────────────────────────────────

_psutil_process: Any | None = None
_psutil_available: bool | None = None


def _sample_process_metrics() -> tuple[int | None, float | None, float | None]:
    """Sample current process memory and CPU via psutil.

    Returns (pid, memory_mb, cpu_percent). All None if psutil unavailable.
    Lazily initializes to avoid import cost at module load.
    """
    global _psutil_process, _psutil_available

    if _psutil_available is None:
        try:
            import psutil
            _psutil_process = psutil.Process(os.getpid())
            _psutil_process.cpu_percent()  # Prime — first call always returns 0.0
            _psutil_available = True
        except (ImportError, OSError):
            _psutil_available = False

    if not _psutil_available:
        return None, None, None

    try:
        mem = _psutil_process.memory_info()
        mem_mb = round(mem.rss / (1024 * 1024), 1)
        cpu_pct = _psutil_process.cpu_percent()
        return _psutil_process.pid, mem_mb, cpu_pct
    except (OSError, AttributeError):
        return None, None, None


# ── Restart Intensity Limiter ──────────────────────────────────


class RestartIntensityLimiter:
    """OTP-style MaxR/MaxT restart limiter.

    If more than max_restarts occur within period_seconds,
    the agent is marked as crash-looping and not restarted.
    This prevents restart storms from destabilizing the system.
    """

    def __init__(self, max_restarts: int = 5, period_seconds: float = 60.0):
        self._max_restarts = max_restarts
        self._period = period_seconds
        self._restart_times: deque[float] = deque()

    @property
    def restart_count(self) -> int:
        """Number of restarts within the current window."""
        self._purge_old()
        return len(self._restart_times)

    def can_restart(self) -> bool:
        """Check if a restart is allowed within intensity limits."""
        self._purge_old()
        return len(self._restart_times) < self._max_restarts

    def record_restart(self) -> None:
        """Record that a restart occurred."""
        self._restart_times.append(time.monotonic())

    def reset(self) -> None:
        """Reset the limiter (e.g., after manual restart)."""
        self._restart_times.clear()

    def _purge_old(self) -> None:
        """Remove restart records outside the window."""
        now = time.monotonic()
        while self._restart_times and (now - self._restart_times[0]) > self._period:
            self._restart_times.popleft()


# ── Agent Handle ───────────────────────────────────────────────


@dataclass
class AgentHandle:
    """Runtime handle for a managed agent instance."""

    spec: AgentSpec
    agent: BaseAgent | None = None
    task: asyncio.Task | None = None
    status: AgentStatus = field(default_factory=lambda: AgentStatus(name=""))
    limiter: RestartIntensityLimiter = field(default_factory=RestartIntensityLimiter)

    def __post_init__(self):
        self.status = AgentStatus(name=self.spec.name)
        self.limiter = RestartIntensityLimiter(
            max_restarts=self.spec.max_restarts,
            period_seconds=self.spec.max_restart_window_sec,
        )

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def is_healthy(self) -> bool:
        if not self.is_running:
            return False
        if self.agent is not None:
            return self.agent.is_healthy()
        return True


# ── Supervisor ─────────────────────────────────────────────────


class Supervisor:
    """OTP-inspired supervisor with Kubernetes reconciliation loop.

    Manages agent lifecycle with:
    - Level-triggered reconciliation (desired state vs actual state)
    - OTP restart strategies (one_for_one, intensity limits)
    - Graceful degradation when Model Bus is unavailable
    - Staggered restarts to prevent thundering herd
    """

    def __init__(
        self,
        config: Config,
        *,
        agent_class_override: type | None = None,
        model_bus_client: object | None = None,
        tool_registry: object | None = None,
        sfs_client: object | None = None,
    ):
        self._config = config
        self._handles: dict[str, AgentHandle] = {}
        self._agent_start_order: list[str] = []  # Tracks start order for rest_for_one
        self._started_at: float = 0.0
        self._total_restarts: int = 0
        self._stopping = False
        self._agent_class_override = agent_class_override
        self._model_bus_client = model_bus_client
        self._tool_registry = tool_registry
        self._sfs_client = sfs_client
        self._log = logger.bind(component="supervisor")

    @property
    def config(self) -> Config:
        return self._config

    @config.setter
    def config(self, new_config: Config) -> None:
        self._config = new_config

    @property
    def handles(self) -> dict[str, AgentHandle]:
        return self._handles

    # ── Reconciliation Loop ─────────────────────────────────────

    async def reconcile(self) -> None:
        """Level-triggered: compare desired state vs actual state, correct drift.

        This is the core of the supervisor — runs every reconcile_interval_sec.
        """
        desired = {
            name: spec
            for name, spec in self._config.agents.items()
            if spec.enabled
        }
        actual = dict(self._handles)

        # Start agents that should be running but aren't
        for name, spec in desired.items():
            handle = actual.get(name)

            if handle is None:
                # New agent — create handle and start
                await self._start_agent(name, spec)

            elif not handle.is_running:
                # Agent crashed — check restart policy
                await self._handle_agent_exit(name, handle)

            elif not handle.is_healthy():
                # Running but unhealthy — update status
                handle.status.state = AgentState.DEGRADED
                self._log.warning("supervisor.agent_degraded", agent=name)

        # Stop agents that are running but no longer desired
        for name in list(actual.keys()):
            if name not in desired:
                await self._stop_agent(name)

    async def _start_agent(self, name: str, spec: AgentSpec) -> None:
        """Start a new agent instance.

        Preserves the restart limiter if the agent was previously tracked
        (i.e., being restarted rather than started for the first time).
        """
        log = self._log.bind(agent=name)
        log.info("supervisor.starting_agent", module=spec.module)

        # Preserve existing limiter across restarts
        existing_limiter = None
        if name in self._handles:
            existing_limiter = self._handles[name].limiter

        try:
            agent_cls = self._agent_class_override or load_agent_class(spec)
            agent = agent_cls(
                spec,
                model_bus_client=self._model_bus_client,
                tool_registry=self._tool_registry,
                sfs_client=self._sfs_client,
            )

            handle = AgentHandle(spec=spec, agent=agent)
            if existing_limiter is not None:
                handle.limiter = existing_limiter
            handle.status.state = AgentState.STARTING
            handle.task = asyncio.create_task(
                self._run_agent_supervised(name, agent, handle),
                name=f"agent-{name}",
            )

            self._handles[name] = handle
            if name not in self._agent_start_order:
                self._agent_start_order.append(name)
            log.info("supervisor.agent_started", cls=agent_cls.__name__)

        except Exception as e:
            log.error("supervisor.start_failed", error=str(e))
            handle = AgentHandle(spec=spec)
            if existing_limiter is not None:
                handle.limiter = existing_limiter
            handle.status.state = AgentState.FAILED
            handle.status.last_error = str(e)
            self._handles[name] = handle

    async def _run_agent_supervised(
        self, name: str, agent: BaseAgent, handle: AgentHandle
    ) -> None:
        """Run an agent with supervision — tracks exit reason."""
        try:
            await agent.run_loop()
            # Clean exit
            handle.status.state = AgentState.STOPPED
            self._log.info("supervisor.agent_stopped", agent=name)
        except asyncio.CancelledError:
            handle.status.state = AgentState.STOPPED
        except Exception as e:
            handle.status.state = AgentState.FAILED
            handle.status.last_error = str(e)
            handle.status.error_count += 1
            self._log.error("supervisor.agent_crashed", agent=name, error=str(e))

    async def _handle_agent_exit(self, name: str, handle: AgentHandle) -> None:
        """Handle an agent that has exited — apply restart policy."""
        spec = handle.spec
        log = self._log.bind(agent=name)

        # Check restart policy
        if spec.restart_policy == RestartPolicy.TEMPORARY:
            log.info("supervisor.agent_temporary_exit", state=handle.status.state.value)
            return

        if spec.restart_policy == RestartPolicy.TRANSIENT:
            if handle.status.state == AgentState.STOPPED:
                log.info("supervisor.agent_clean_exit")
                return

        # Check intensity limiter
        if not handle.limiter.can_restart():
            handle.status.state = AgentState.CRASH_LOOP
            log.error(
                "supervisor.crash_loop_detected",
                restarts=handle.limiter.restart_count,
                window_sec=spec.max_restart_window_sec,
            )
            return

        # Restart the agent
        handle.limiter.record_restart()
        self._total_restarts += 1
        log.info(
            "supervisor.restarting_agent",
            restart_count=handle.limiter.restart_count,
            policy=spec.restart_policy.value,
        )

        # rest_for_one: stop dependents BEFORE restarting the crashed agent
        # OTP semantics: stop in reverse order, then restart all in original order
        if self._config.supervisor.restart_strategy == RestartStrategy.REST_FOR_ONE:
            await self._stop_agents_after(name)

        # Small jitter to prevent thundering herd
        jitter = (hash(name) % 1000) / 1000.0  # 0-1 second
        await asyncio.sleep(jitter)

        await self._start_agent(name, spec)

        # rest_for_one: restart dependents AFTER the crashed agent is back up
        if self._config.supervisor.restart_strategy == RestartStrategy.REST_FOR_ONE:
            await self._restart_agents_after(name)

    def _get_agents_after(self, name: str) -> list[str]:
        """Get list of agents started after the given agent (for rest_for_one)."""
        if name not in self._agent_start_order:
            return []
        idx = self._agent_start_order.index(name)
        return self._agent_start_order[idx + 1:]

    async def _stop_agents_after(self, name: str) -> None:
        """rest_for_one phase 1: stop all agents started after the given agent.

        Stops in reverse start order per OTP semantics.
        """
        agents_after = self._get_agents_after(name)
        if not agents_after:
            return

        self._log.info(
            "supervisor.rest_for_one.stopping_dependents",
            crashed=name,
            stopping=list(reversed(agents_after)),
        )

        for agent_name in reversed(agents_after):
            if agent_name in self._handles and self._handles[agent_name].is_running:
                await self._stop_agent(agent_name)

    async def _restart_agents_after(self, name: str) -> None:
        """rest_for_one phase 2: restart all agents started after the given agent.

        Restarts in original start order per OTP semantics.
        Called AFTER the crashed agent has been restarted.
        """
        agents_after = self._get_agents_after(name)
        if not agents_after:
            return

        self._log.info(
            "supervisor.rest_for_one.restarting_dependents",
            crashed=name,
            restarting=agents_after,
        )

        for agent_name in agents_after:
            spec = self._config.agents.get(agent_name)
            if spec and spec.enabled:
                await self._start_agent(agent_name, spec)

    async def _stop_agent(self, name: str) -> None:
        """Gracefully stop an agent."""
        handle = self._handles.get(name)
        if handle is None:
            return

        log = self._log.bind(agent=name)
        log.info("supervisor.stopping_agent")

        if handle.agent is not None:
            handle.agent.request_stop()

        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
            try:
                await asyncio.wait_for(handle.task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        handle.status.state = AgentState.STOPPED
        del self._handles[name]
        log.info("supervisor.agent_removed")

    # ── Public API ──────────────────────────────────────────────

    async def start_agent(self, name: str) -> None:
        """Manually start an agent by name."""
        spec = self._config.agents.get(name)
        if spec is None:
            from yaaos_agentd.errors import AgentNotFoundError
            raise AgentNotFoundError(name)

        handle = self._handles.get(name)
        if handle and handle.is_running:
            from yaaos_agentd.errors import AgentAlreadyRunningError
            raise AgentAlreadyRunningError(name)

        # Reset limiter on manual start
        if handle:
            handle.limiter.reset()

        await self._start_agent(name, spec)

    async def stop_agent(self, name: str) -> None:
        """Manually stop an agent by name."""
        if name not in self._handles:
            from yaaos_agentd.errors import AgentNotFoundError
            raise AgentNotFoundError(name)
        await self._stop_agent(name)

    async def restart_agent(self, name: str) -> None:
        """Manually restart an agent by name."""
        if name in self._handles:
            handle = self._handles[name]
            handle.limiter.reset()  # Manual restart resets limiter
            await self._stop_agent(name)

        spec = self._config.agents.get(name)
        if spec is None:
            from yaaos_agentd.errors import AgentNotFoundError
            raise AgentNotFoundError(name)
        await self._start_agent(name, spec)

    def get_agent_status(self, name: str) -> AgentStatus | None:
        """Get status for a specific agent.

        Returns the agent's own status if it's running (more detailed),
        otherwise returns the supervisor's handle status.
        """
        handle = self._handles.get(name)
        if handle is None:
            return None
        if handle.agent is not None and handle.is_running:
            return handle.agent.status
        return handle.status

    def get_all_statuses(self) -> dict[str, AgentStatus]:
        """Get status for all agents."""
        result = {}
        for name, handle in self._handles.items():
            if handle.agent is not None and handle.is_running:
                result[name] = handle.agent.status
            else:
                result[name] = handle.status
        return result

    def get_health(self) -> SupervisorHealth:
        """Get supervisor health summary including process metrics."""
        statuses = self.get_all_statuses()
        running = sum(1 for s in statuses.values() if s.state == AgentState.RUNNING)
        failed = sum(
            1 for s in statuses.values()
            if s.state in (AgentState.FAILED, AgentState.CRASH_LOOP)
        )
        degraded = sum(1 for s in statuses.values() if s.state == AgentState.DEGRADED)
        total_cycles = sum(s.cycle_count for s in statuses.values())

        if failed > 0:
            status = "degraded"
        elif degraded > 0:
            status = "degraded"
        else:
            status = "healthy"

        # Sample supervisor process metrics
        pid, mem_mb, cpu_pct = _sample_process_metrics()

        return SupervisorHealth(
            status=status,
            uptime_sec=time.monotonic() - self._started_at if self._started_at else 0,
            agent_count=len(statuses),
            agents_running=running,
            agents_failed=failed,
            agents_degraded=degraded,
            total_cycles=total_cycles,
            total_restarts=self._total_restarts,
            process_memory_mb=mem_mb,
            process_cpu_percent=cpu_pct,
            pid=pid,
        )

    # ── Main Loop ───────────────────────────────────────────────

    async def run(self) -> None:
        """Run the supervisor reconciliation loop."""
        self._started_at = time.monotonic()
        self._log.info(
            "supervisor.starting",
            agents=list(self._config.agents.keys()),
            interval_sec=self._config.supervisor.reconcile_interval_sec,
        )

        while not self._stopping:
            try:
                await self.reconcile()
            except Exception:
                self._log.exception("supervisor.reconcile_failed")

            try:
                await asyncio.sleep(self._config.supervisor.reconcile_interval_sec)
            except asyncio.CancelledError:
                break

    async def shutdown(self) -> None:
        """Gracefully stop all agents and the supervisor."""
        self._stopping = True
        self._log.info("supervisor.shutting_down", agents=len(self._handles))

        # Stop all agents concurrently
        tasks = [self._stop_agent(name) for name in list(self._handles.keys())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._log.info(
            "supervisor.stopped",
            total_restarts=self._total_restarts,
        )


# ── Daemon Entry Point ─────────────────────────────────────────


def _running_under_systemd() -> bool:
    """Check if we're running as a systemd Type=notify service."""
    import os
    return bool(os.environ.get("NOTIFY_SOCKET"))


def _sd_notify(msg: str) -> None:
    """Send a systemd notification.

    When running under systemd (NOTIFY_SOCKET set), failing to notify
    is a real problem — systemd will consider the service timed-out.
    Warn loudly if sdnotify is missing in that case.
    """
    try:
        import sdnotify
        sdnotify.SystemdNotifier().notify(msg)
    except ImportError:
        if _running_under_systemd():
            logger.warning(
                "daemon.sdnotify_missing",
                msg=msg,
                hint="Install sdnotify: pip install sdnotify. "
                "Without it, systemd Type=notify will timeout.",
            )


async def _watchdog_loop(interval: float) -> None:
    """Periodically ping the systemd watchdog at half the configured interval."""
    while True:
        _sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


async def run_daemon(config: Config, *, config_path: Path | None = None) -> None:
    """Run the SystemAgentd supervisor daemon."""
    _configure_logging(config.supervisor.log_level)
    log = logger.bind(component="daemon")
    log.info(
        "daemon.starting",
        socket=str(config.supervisor.socket_path),
        version="0.1.0",
    )

    _sd_notify("STATUS=Initializing agent supervisor...")

    supervisor = Supervisor(config)

    # Initialize Tool Registry
    from yaaos_agentd.tools.registry import ToolRegistry
    registry = ToolRegistry(config.tool_dirs)

    # Start Agent Bus JSON-RPC server
    from yaaos_agentd.server import AgentBusServer
    bus_server = AgentBusServer(
        config.supervisor.socket_path,
        supervisor,
        registry,
        max_connections=config.supervisor.max_connections,
        config_path=config_path,
    )
    await bus_server.start()

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

    # Start supervisor reconciliation loop
    supervisor_task = asyncio.create_task(supervisor.run())

    # Start watchdog pinger if WATCHDOG_USEC is set
    import os
    watchdog_task = None
    watchdog_usec = int(os.environ.get("WATCHDOG_USEC", 0))
    if watchdog_usec > 0:
        interval = watchdog_usec / 1_000_000 / 2  # Ping at half the WatchdogSec
        watchdog_task = asyncio.create_task(_watchdog_loop(interval))
        log.info("daemon.watchdog_enabled", interval_sec=round(interval, 1))

    # Signal readiness — ONLY after everything is fully up
    _sd_notify("READY=1")
    _sd_notify(f"STATUS=Managing {len(config.agents)} agents, {len(registry.tools)} tools")

    log.info(
        "daemon.ready",
        agents=list(config.agents.keys()),
        tools=len(registry.tools),
    )

    # Main loop: wait for shutdown or reload
    while not shutdown.is_set():
        reload_t = asyncio.create_task(reload_event.wait())
        shutdown_t = asyncio.create_task(shutdown.wait())
        done, pending = await asyncio.wait(
            [reload_t, shutdown_t],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        if reload_event.is_set() and not shutdown.is_set():
            reload_event.clear()
            log.info("daemon.reloading_config")
            _sd_notify("RELOADING=1")
            _sd_notify("STATUS=Reloading configuration...")
            try:
                new_config = Config.load(config_path)
                supervisor.config = new_config
                await supervisor.reconcile()
                _sd_notify("READY=1")
                _sd_notify(f"STATUS=Managing {len(new_config.agents)} agents")
                log.info("daemon.config_reloaded")
            except Exception:
                log.exception("daemon.reload_failed")
                _sd_notify("READY=1")  # Must follow RELOADING=1 even on failure
                _sd_notify("STATUS=Config reload failed, running with previous config")

    # Shutdown
    log.info("daemon.shutting_down")
    _sd_notify("STOPPING=1")
    _sd_notify("STATUS=Shutting down...")

    # Cancel watchdog first
    if watchdog_task is not None:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

    supervisor_task.cancel()
    try:
        await supervisor_task
    except asyncio.CancelledError:
        pass

    await bus_server.stop()
    await supervisor.shutdown()
    log.info("daemon.stopped")


def main() -> None:
    """CLI entry point for systemagentd."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import click

    @click.command()
    @click.option(
        "--config",
        "-c",
        "config_path",
        type=click.Path(exists=False),
        default=None,
        help="Path to config file (default: ~/.config/yaaos/agentd.toml)",
    )
    def _main(config_path: str | None):
        """Start the YAAOS SystemAgentd supervisor daemon."""
        resolved_path = Path(config_path) if config_path else None
        config = Config.load(resolved_path)

        try:
            asyncio.run(run_daemon(config, config_path=resolved_path))
        except KeyboardInterrupt:
            pass

    _main()


if __name__ == "__main__":
    main()
