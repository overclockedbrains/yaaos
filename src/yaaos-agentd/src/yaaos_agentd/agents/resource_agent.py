"""Resource-Agent — CPU/RAM/GPU monitoring with trend prediction.

Monitors system resources via psutil, detects threshold violations,
and predicts resource exhaustion using exponential weighted moving averages.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import structlog

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.types import Action, ActionResult, AgentSpec

logger = structlog.get_logger()

# EWMA smoothing factor
_EWMA_ALPHA = 0.2


@dataclass
class ResourceSnapshot:
    """Point-in-time resource measurement."""

    timestamp: float
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_available_mb: float = 0.0
    memory_total_mb: float = 0.0
    gpu_util_percent: float | None = None
    gpu_vram_used_mb: float | None = None
    gpu_vram_total_mb: float | None = None
    gpu_temp_c: float | None = None


@dataclass
class ResourceTrend:
    """Exponential weighted moving average for trend prediction."""

    history: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=360)
    )  # ~1 hour at 10s intervals
    ewma: float = 0.0

    def update(self, timestamp: float, value: float) -> None:
        self.history.append((timestamp, value))
        if self.ewma == 0.0:
            self.ewma = value
        else:
            self.ewma = _EWMA_ALPHA * value + (1 - _EWMA_ALPHA) * self.ewma

    def predict_time_to_threshold(self, threshold: float) -> float | None:
        """Predict seconds until value reaches threshold based on EWMA trend.

        Returns None if trend is flat or declining, or if threshold already exceeded.
        """
        if len(self.history) < 10:
            return None

        # Calculate slope from recent EWMA trend
        recent = list(self.history)[-30:]
        if len(recent) < 5:
            return None

        t0, v0 = recent[0]
        t1, v1 = recent[-1]
        dt = t1 - t0
        if dt == 0:
            return None

        slope = (v1 - v0) / dt  # units per second
        if slope <= 0:
            return None  # Decreasing or flat

        remaining = threshold - v1
        if remaining <= 0:
            return 0.0  # Already exceeded

        return remaining / slope


class ResourceAgent(BaseAgent):
    """Monitors CPU, RAM, and GPU resources with predictive alerting.

    Tier 1: Threshold-based alerts (CPU > 85%, memory < 10% available).
    Tier 2: Trend-based prediction (EWMA of memory → predict OOM).
    Tier 3: LLM analysis (on critical only — suggest remediation).
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self._cpu_threshold: float = spec.config.get("cpu_threshold", 85.0)
        self._memory_warn_pct: float = spec.config.get("memory_warn_pct", 80.0)
        self._memory_critical_pct: float = spec.config.get("memory_critical_pct", 90.0)
        self._prediction_window_sec: float = spec.config.get("prediction_window_sec", 180.0)
        self._llm_enabled: bool = spec.config.get("llm_enabled", False)
        self._gpu_enabled: bool = spec.config.get("gpu_enabled", True)

        # Trends for prediction
        self._memory_trend = ResourceTrend()
        self._cpu_trend = ResourceTrend()
        self._last_snapshot: ResourceSnapshot | None = None
        self._sustained_cpu_start: float | None = None

    async def observe(self) -> dict:
        """Gather system resource metrics."""
        now = time.monotonic()
        snapshot = ResourceSnapshot(timestamp=now)

        try:
            import psutil

            snapshot.cpu_percent = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            snapshot.memory_percent = mem.percent
            snapshot.memory_available_mb = mem.available / (1024 * 1024)
            snapshot.memory_total_mb = mem.total / (1024 * 1024)

        except ImportError:
            self._log.debug("resource_agent.psutil_unavailable")
        except Exception as e:
            self._log.warning("resource_agent.psutil_error", error=str(e))

        # GPU metrics (optional)
        if self._gpu_enabled:
            try:
                import pynvml
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

                snapshot.gpu_util_percent = util.gpu
                snapshot.gpu_vram_used_mb = mem_info.used / (1024 * 1024)
                snapshot.gpu_vram_total_mb = mem_info.total / (1024 * 1024)
                snapshot.gpu_temp_c = temp
                pynvml.nvmlShutdown()
            except (ImportError, Exception):
                pass  # GPU monitoring is optional

        # Update trends
        self._memory_trend.update(now, snapshot.memory_percent)
        self._cpu_trend.update(now, snapshot.cpu_percent)
        self._last_snapshot = snapshot

        result = {
            "cpu_percent": snapshot.cpu_percent,
            "memory_percent": snapshot.memory_percent,
            "memory_available_mb": round(snapshot.memory_available_mb, 0),
            "memory_total_mb": round(snapshot.memory_total_mb, 0),
        }

        if snapshot.gpu_util_percent is not None:
            result["gpu_util_percent"] = snapshot.gpu_util_percent
            result["gpu_vram_used_mb"] = round(snapshot.gpu_vram_used_mb or 0, 0)
            result["gpu_temp_c"] = snapshot.gpu_temp_c

        return result

    async def reason(self, observation: dict) -> list[Action]:
        """Apply tiered reasoning to resource observations."""
        actions: list[Action] = []
        now = time.monotonic()

        cpu = observation.get("cpu_percent", 0)
        mem_pct = observation.get("memory_percent", 0)

        # ── Tier 1: Threshold alerts ────────────────────────────

        # CPU sustained high
        if cpu > self._cpu_threshold:
            if self._sustained_cpu_start is None:
                self._sustained_cpu_start = now
            elif now - self._sustained_cpu_start > 60:
                actions.append(Action(
                    tool="alert",
                    action="cpu_high",
                    params={"cpu_percent": cpu, "sustained_sec": round(now - self._sustained_cpu_start)},
                    description=f"CPU sustained at {cpu:.0f}% for {now - self._sustained_cpu_start:.0f}s",
                ))
        else:
            self._sustained_cpu_start = None

        # Memory thresholds
        if mem_pct >= self._memory_critical_pct:
            actions.append(Action(
                tool="alert",
                action="memory_critical",
                params={
                    "memory_percent": mem_pct,
                    "available_mb": observation.get("memory_available_mb", 0),
                },
                description=f"Critical: memory at {mem_pct:.0f}%",
            ))
        elif mem_pct >= self._memory_warn_pct:
            actions.append(Action(
                tool="alert",
                action="memory_warning",
                params={"memory_percent": mem_pct},
                description=f"Warning: memory at {mem_pct:.0f}%",
            ))

        # ── Tier 2: Trend prediction ────────────────────────────

        time_to_critical = self._memory_trend.predict_time_to_threshold(
            self._memory_critical_pct
        )
        if (
            time_to_critical is not None
            and 0 < time_to_critical < self._prediction_window_sec
        ):
            actions.append(Action(
                tool="alert",
                action="memory_predicted",
                params={
                    "predicted_sec": round(time_to_critical),
                    "current_percent": mem_pct,
                    "trend_ewma": round(self._memory_trend.ewma, 1),
                },
                description=f"Memory exhaustion predicted in {time_to_critical:.0f}s",
            ))

        # ── Tier 3: LLM analysis on critical ────────────────────

        if self._llm_enabled and self.model_bus:
            critical_actions = [a for a in actions if "critical" in a.action or "predicted" in a.action]
            if critical_actions:
                actions.append(Action(
                    tool="model_bus",
                    action="analyze_resources",
                    params={
                        "cpu_percent": cpu,
                        "memory_percent": mem_pct,
                        "alerts": [a.description for a in critical_actions],
                    },
                    description="LLM resource analysis",
                ))

        return actions

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute alerts and analysis actions."""
        results: list[ActionResult] = []

        for action in actions:
            start = time.monotonic()

            if action.tool == "alert":
                level = "error" if "critical" in action.action else "warning"
                getattr(self._log, level)(
                    f"resource_agent.{action.action}",
                    **{k: v for k, v in action.params.items() if isinstance(v, (int, float, str))},
                )
                results.append(ActionResult(
                    action=action,
                    success=True,
                    output=action.description,
                    duration_ms=(time.monotonic() - start) * 1000,
                ))

            elif action.tool == "model_bus" and self.model_bus:
                try:
                    alerts_text = "\n".join(action.params.get("alerts", []))
                    prompt = (
                        f"System resource alert. "
                        f"CPU: {action.params.get('cpu_percent', '?')}%, "
                        f"Memory: {action.params.get('memory_percent', '?')}%.\n"
                        f"Alerts:\n{alerts_text}\n\n"
                        f"List the top resource consumers and suggest remediation actions."
                    )
                    # model_bus.generate() returns an async iterator of chunks
                    text_parts: list[str] = []
                    async for chunk in self.model_bus.generate(prompt, stream=False):
                        if chunk.get("done"):
                            text_parts.append(chunk.get("text", ""))
                            break
                        if "token" in chunk:
                            text_parts.append(chunk["token"])
                    analysis = "".join(text_parts)
                    results.append(ActionResult(
                        action=action,
                        success=True,
                        output=analysis[:1000],
                        duration_ms=(time.monotonic() - start) * 1000,
                    ))
                except Exception as e:
                    results.append(ActionResult(
                        action=action,
                        success=False,
                        error=str(e),
                        duration_ms=(time.monotonic() - start) * 1000,
                    ))
            else:
                results.append(ActionResult(
                    action=action,
                    success=True,
                    output="No handler",
                    duration_ms=(time.monotonic() - start) * 1000,
                ))

        return results
