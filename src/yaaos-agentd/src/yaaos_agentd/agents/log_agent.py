"""Log-Agent — real-time journald analysis with anomaly detection.

Streams system logs, detects anomalies via rule-based + statistical tiers,
and optionally escalates to LLM analysis via Model Bus.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.types import Action, ActionResult, AgentSpec

logger = structlog.get_logger()

# Known critical patterns (rule-based fast path)
_CRITICAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Out of memory", re.IGNORECASE), "oom"),
    (re.compile(r"OOM killer", re.IGNORECASE), "oom"),
    (re.compile(r"segfault", re.IGNORECASE), "segfault"),
    (re.compile(r"kernel panic", re.IGNORECASE), "kernel_panic"),
    (re.compile(r"Connection refused", re.IGNORECASE), "connection_refused"),
    (re.compile(r"Permission denied", re.IGNORECASE), "permission_denied"),
    (re.compile(r"service failed", re.IGNORECASE), "service_failed"),
    (re.compile(r"disk full", re.IGNORECASE), "disk_full"),
    (re.compile(r"No space left on device", re.IGNORECASE), "disk_full"),
]

# Default window for moving average (seconds)
_RATE_WINDOW_SEC = 300.0  # 5 minutes


@dataclass
class UnitStats:
    """Per-unit log rate statistics for anomaly detection."""

    entry_count: int = 0
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=1000))

    @property
    def rate_per_min(self) -> float:
        """Entries per minute over the window."""
        if len(self.timestamps) < 2:
            return 0.0
        now = time.monotonic()
        cutoff = now - _RATE_WINDOW_SEC
        recent = [t for t in self.timestamps if t > cutoff]
        if len(recent) < 2:
            return 0.0
        span = now - recent[0]
        if span == 0:
            return 0.0
        return len(recent) / (span / 60.0)


class LogAgent(BaseAgent):
    """Monitors journald for anomalies using tiered reasoning.

    Tier 1 (rule-based): Matches critical log patterns immediately.
    Tier 2 (statistical): Detects log rate spikes above moving average.
    Tier 3 (LLM): Sends anomalous batches to Model Bus for analysis.
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self._unit_stats: dict[str, UnitStats] = {}
        self._entry_buffer: list[dict] = []
        self._journal_reader: Any = None
        self._units: list[str] = spec.config.get("units", [])
        self._rate_threshold: float = spec.config.get("rate_threshold", 3.0)
        self._llm_enabled: bool = spec.config.get("llm_enabled", False)
        self._baseline_rates: dict[str, float] = {}

    async def on_start(self) -> None:
        self._try_connect_journal()

    def _try_connect_journal(self) -> None:
        """Attempt to connect to journald. Gracefully degrade if unavailable."""
        try:
            from systemd import journal
            self._journal_reader = journal.Reader()
            if self._units:
                for unit in self._units:
                    self._journal_reader.add_match(_SYSTEMD_UNIT=unit)
            self._journal_reader.seek_tail()
            self._journal_reader.get_previous()
            self._log.info("log_agent.journal_connected", units=self._units)
        except ImportError:
            self._log.warning("log_agent.journal_unavailable", reason="systemd module not installed")
            self._journal_reader = None
        except Exception as e:
            self._log.warning("log_agent.journal_error", error=str(e))
            self._journal_reader = None

    async def observe(self) -> dict:
        """Read new journal entries since last cycle."""
        entries: list[dict] = []
        now = time.monotonic()

        if self._journal_reader is not None:
            try:
                for entry in self._journal_reader:
                    unit = entry.get("_SYSTEMD_UNIT", "unknown")
                    message = entry.get("MESSAGE", "")
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="replace")

                    entries.append({
                        "unit": unit,
                        "message": str(message),
                        "priority": entry.get("PRIORITY", 6),
                        "timestamp": str(entry.get("__REALTIME_TIMESTAMP", "")),
                    })

                    # Update per-unit stats
                    stats = self._unit_stats.setdefault(unit, UnitStats())
                    stats.entry_count += 1
                    stats.timestamps.append(now)

            except Exception as e:
                self._log.warning("log_agent.read_error", error=str(e))

        self._entry_buffer = entries
        return {
            "entry_count": len(entries),
            "units_seen": list({e["unit"] for e in entries}),
        }

    async def reason(self, observation: dict) -> list[Action]:
        """Apply tiered reasoning to detect anomalies."""
        actions: list[Action] = []

        # Tier 1: Rule-based critical pattern matching
        for entry in self._entry_buffer:
            for pattern, alert_type in _CRITICAL_PATTERNS:
                if pattern.search(entry["message"]):
                    actions.append(Action(
                        tool="alert",
                        action="critical",
                        params={
                            "unit": entry["unit"],
                            "alert_type": alert_type,
                            "message": entry["message"][:500],
                        },
                        description=f"Critical: {alert_type} in {entry['unit']}",
                    ))
                    break  # One alert per entry

        # Tier 2: Statistical rate anomaly detection
        for unit, stats in self._unit_stats.items():
            current_rate = stats.rate_per_min
            baseline = self._baseline_rates.get(unit, 0.0)

            if baseline > 0 and current_rate > baseline * self._rate_threshold:
                actions.append(Action(
                    tool="alert",
                    action="rate_spike",
                    params={
                        "unit": unit,
                        "current_rate": round(current_rate, 1),
                        "baseline_rate": round(baseline, 1),
                        "multiplier": round(current_rate / baseline, 1),
                    },
                    description=f"Log rate spike: {unit} at {current_rate:.0f}/min (baseline {baseline:.0f}/min)",
                ))

            # Update baseline (exponential moving average)
            if baseline == 0:
                self._baseline_rates[unit] = current_rate
            else:
                alpha = 0.1
                self._baseline_rates[unit] = alpha * current_rate + (1 - alpha) * baseline

        # Tier 3: LLM analysis for anomalous batches
        if self._llm_enabled and self.model_bus and actions:
            anomalous_entries = self._entry_buffer[-50:]  # Last 50 entries
            if anomalous_entries:
                actions.append(Action(
                    tool="model_bus",
                    action="analyze",
                    params={
                        "entries": [e["message"] for e in anomalous_entries[:20]],
                    },
                    description="LLM analysis of anomalous log batch",
                ))

        return actions

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute alert and analysis actions."""
        results: list[ActionResult] = []

        for action in actions:
            start = time.monotonic()

            if action.tool == "alert":
                self._log.warning(
                    "log_agent.alert",
                    alert_type=action.params.get("alert_type", action.action),
                    unit=action.params.get("unit"),
                    message=action.params.get("message", "")[:200],
                )
                results.append(ActionResult(
                    action=action,
                    success=True,
                    output=f"Alert: {action.description}",
                    duration_ms=(time.monotonic() - start) * 1000,
                ))

            elif action.tool == "model_bus" and self.model_bus:
                try:
                    entries_text = "\n".join(action.params.get("entries", []))
                    prompt = (
                        f"Analyze these system log entries and identify anomalies, "
                        f"root causes, and suggested fixes:\n\n{entries_text}"
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
