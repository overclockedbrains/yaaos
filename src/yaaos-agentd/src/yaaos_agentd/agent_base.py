"""Base class for all YAAOS agents.

Every agent implements the observe() → reason() → act() cycle.
The BaseAgent provides lifecycle management, state persistence hooks,
and integration points for Model Bus, Tool Registry, and SFS.
"""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

from yaaos_agentd.types import Action, ActionResult, AgentSpec, AgentState, AgentStatus

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


class _ProcessMetrics:
    """Samples process-level metrics via psutil.

    Lazily initializes psutil.Process to avoid import cost when
    psutil is unavailable (e.g., minimal containers).
    """

    def __init__(self) -> None:
        self._process: Any | None = None
        self._available: bool | None = None

    def _init(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import psutil

            self._process = psutil.Process(os.getpid())
            # Prime cpu_percent — first call always returns 0.0
            self._process.cpu_percent()
            self._available = True
        except (ImportError, OSError):
            self._available = False
        return self._available

    def sample(self, status: AgentStatus) -> None:
        """Update status with current process memory and CPU."""
        if not self._init():
            return
        try:
            mem = self._process.memory_info()
            status.memory_bytes = mem.rss
            status.cpu_percent = self._process.cpu_percent()
            status.pid = self._process.pid
        except (OSError, AttributeError):
            pass


# Module-level singleton — one Process handle per interpreter,
# shared across all agents in the same process.
_metrics = _ProcessMetrics()


class BaseAgent(ABC):
    """Abstract base class for YAAOS agents.

    Subclasses implement observe(), reason(), and act() to define
    agent-specific behavior. The base class handles:
    - Reconciliation loop (run_loop)
    - Cycle execution (run_cycle)
    - State tracking and reporting
    - Lifecycle hooks (on_start, on_stop, on_reload)
    """

    def __init__(
        self,
        spec: AgentSpec,
        *,
        model_bus_client: Any | None = None,
        tool_registry: Any | None = None,
        sfs_client: Any | None = None,
    ):
        self.spec = spec
        self.model_bus = model_bus_client
        self.tool_registry = tool_registry
        self.sfs = sfs_client

        # Runtime state
        self._status = AgentStatus(name=spec.name)
        self._state: dict[str, Any] = {}  # Agent working memory
        self._stopping = False
        self._log = logger.bind(agent=spec.name)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def state(self) -> dict[str, Any]:
        """Agent working memory — survives across cycles, lost on crash."""
        return self._state

    # ── Abstract methods (agents must implement) ────────────────

    @abstractmethod
    async def observe(self) -> dict:
        """Gather current system state relevant to this agent.

        Returns an observation dict that will be passed to reason().
        Should be fast and non-blocking.
        """
        ...

    @abstractmethod
    async def reason(self, observation: dict) -> list[Action]:
        """Analyze the observation and decide what actions to take.

        Uses tiered approach:
        1. Rule-based fast path (no LLM)
        2. Statistical anomaly detection (no LLM)
        3. LLM reasoning via Model Bus (only when needed)

        Returns a list of actions (possibly empty).
        """
        ...

    @abstractmethod
    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute planned actions via Tool Registry.

        Returns results for logging and state update.
        """
        ...

    # ── Lifecycle hooks (optional overrides) ────────────────────

    async def on_start(self) -> None:
        """Called once when the agent starts, before the first cycle."""

    async def on_stop(self) -> None:
        """Called once during graceful shutdown, after the last cycle."""

    async def on_reload(self, new_config: dict) -> None:
        """Called when agent configuration is hot-reloaded via SIGHUP."""

    # ── State persistence hooks (optional overrides) ────────────

    async def save_state(self) -> None:
        """Persist agent state to disk (e.g., SQLite).

        Called periodically and on graceful shutdown.
        Override in agents that need state across restarts.
        """

    async def load_state(self) -> None:
        """Load persisted state from disk.

        Called once during on_start.
        Override in agents that need state across restarts.
        """

    # ── Core execution ──────────────────────────────────────────

    async def run_cycle(self) -> None:
        """Execute a single observe → reason → act cycle."""
        cycle_start = time.monotonic()

        try:
            observation = await self.observe()
            actions = await self.reason(observation)

            results: list[ActionResult] = []
            if actions:
                results = await self.act(actions)
                await self._report(observation, actions, results)

            self._status.cycle_count += 1
            self._status.last_cycle_at = time.monotonic()

            if actions and results:
                # Summarize last action for status display
                last = results[-1]
                self._status.last_action = last.action.description or (
                    f"{last.action.tool}.{last.action.action}"
                )

            # Sample process metrics after each cycle
            _metrics.sample(self._status)

            elapsed_ms = (time.monotonic() - cycle_start) * 1000
            self._log.debug(
                "agent.cycle_completed",
                cycle=self._status.cycle_count,
                actions=len(actions),
                elapsed_ms=round(elapsed_ms, 1),
            )

        except Exception as e:
            self._status.error_count += 1
            self._status.last_error = str(e)
            self._log.exception("agent.cycle_failed", cycle=self._status.cycle_count)
            raise

    async def run_loop(self) -> None:
        """Run the agent's reconciliation loop until stopped.

        This is the main entry point for long-running agents.
        Each iteration runs a single observe→reason→act cycle,
        then sleeps for the configured reconcile interval.
        """
        self._log.info("agent.starting")
        self._status.state = AgentState.STARTING
        self._status.started_at = time.monotonic()

        try:
            await self.load_state()
            await self.on_start()
            self._status.state = AgentState.RUNNING
            self._log.info("agent.running", interval_sec=self.spec.reconcile_interval_sec)

            while not self._stopping:
                try:
                    await self.run_cycle()
                except Exception:
                    # Cycle errors are logged but don't stop the loop.
                    # The supervisor monitors agent health externally.
                    pass

                # Sleep with cancellation support
                try:
                    await asyncio.sleep(self.spec.reconcile_interval_sec)
                except asyncio.CancelledError:
                    break

        except asyncio.CancelledError:
            pass
        finally:
            self._status.state = AgentState.STOPPING
            self._log.info("agent.stopping")
            try:
                await self.save_state()
                await self.on_stop()
            except Exception:
                self._log.exception("agent.stop_failed")
            self._status.state = AgentState.STOPPED
            self._log.info("agent.stopped", cycles=self._status.cycle_count)

    def request_stop(self) -> None:
        """Request the agent to stop gracefully at the next cycle boundary."""
        self._stopping = True

    async def _report(
        self,
        observation: dict,
        actions: list[Action],
        results: list[ActionResult],
    ) -> None:
        """Log cycle results with structured fields."""
        for result in results:
            level = "info" if result.success else "warning"
            getattr(self._log, level)(
                "agent.action",
                tool=result.action.tool,
                action=result.action.action,
                success=result.success,
                duration_ms=round(result.duration_ms, 1),
                error=result.error,
            )

    # ── Health check ────────────────────────────────────────────

    def is_healthy(self) -> bool:
        """Check if the agent is considered healthy.

        An agent is healthy if:
        - It's in RUNNING state
        - It has completed at least one cycle
        - It hasn't exceeded error thresholds
        """
        if self._status.state != AgentState.RUNNING:
            return False
        if self._status.cycle_count == 0:
            return True  # Just started, hasn't had a chance to run yet
        # Degraded if >50% of recent cycles errored
        if self._status.cycle_count > 0:
            error_rate = self._status.error_count / self._status.cycle_count
            if error_rate > 0.5:
                return False
        return True
