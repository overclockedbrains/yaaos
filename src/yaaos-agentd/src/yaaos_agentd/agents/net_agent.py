"""Net-Agent — network connection monitoring and anomaly detection.

Monitors /proc/net for connection states, detects unusual listening ports,
and flags connection rate anomalies.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.types import Action, ActionResult, AgentSpec

logger = structlog.get_logger()

# TCP connection states from /proc/net/tcp
_TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}

# Well-known ports that are expected to listen
_EXPECTED_LISTENERS = {22, 53, 80, 443, 631, 5353, 8080}


@dataclass
class ConnectionSnapshot:
    """Snapshot of current network connections."""

    timestamp: float
    established: int = 0
    listening: int = 0
    time_wait: int = 0
    total: int = 0
    listening_ports: set[int] = field(default_factory=set)
    connections: list[dict] = field(default_factory=list)


class NetAgent(BaseAgent):
    """Monitors network connections for anomalies.

    Tier 1 (rule-based): Detects new unexpected listening ports.
    Tier 2 (statistical): Connection rate deviation from baseline.
    Tier 3 (LLM): Anomalous connection pattern analysis.
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self._rate_threshold: float = spec.config.get("rate_threshold", 2.0)
        self._llm_enabled: bool = spec.config.get("llm_enabled", False)
        self._expected_ports: set[int] = set(spec.config.get("expected_ports", []))
        self._expected_ports |= _EXPECTED_LISTENERS

        self._known_listeners: set[int] = set()
        self._connection_rates: deque[tuple[float, int]] = deque(maxlen=360)
        self._baseline_rate: float = 0.0
        self._first_cycle: bool = True

    async def observe(self) -> dict:
        """Parse /proc/net/tcp and /proc/net/tcp6 for connection state."""
        now = time.monotonic()
        snapshot = ConnectionSnapshot(timestamp=now)

        for proc_path in ["/proc/net/tcp", "/proc/net/tcp6"]:
            path = Path(proc_path)
            if not path.exists():
                continue

            try:
                content = path.read_text()
                for line in content.strip().split("\n")[1:]:  # Skip header
                    parts = line.split()
                    if len(parts) < 4:
                        continue

                    local_addr = parts[1]
                    state_hex = parts[3]

                    state = _TCP_STATES.get(state_hex, "UNKNOWN")
                    local_port = int(local_addr.split(":")[1], 16)

                    snapshot.total += 1

                    if state == "ESTABLISHED":
                        snapshot.established += 1
                    elif state == "LISTEN":
                        snapshot.listening += 1
                        snapshot.listening_ports.add(local_port)
                    elif state == "TIME_WAIT":
                        snapshot.time_wait += 1

            except PermissionError:
                self._log.debug("net_agent.permission_denied", path=proc_path)
            except Exception as e:
                self._log.warning("net_agent.parse_error", path=proc_path, error=str(e))

        # Track connection rate
        self._connection_rates.append((now, snapshot.established))

        return {
            "established": snapshot.established,
            "listening": snapshot.listening,
            "time_wait": snapshot.time_wait,
            "total": snapshot.total,
            "listening_ports": sorted(snapshot.listening_ports),
        }

    async def reason(self, observation: dict) -> list[Action]:
        """Detect network anomalies using tiered reasoning."""
        actions: list[Action] = []
        current_ports = set(observation.get("listening_ports", []))

        # ── Tier 1: New unexpected listeners ─────────────────────

        new_ports = current_ports - self._known_listeners - self._expected_ports
        if new_ports and not self._first_cycle:
            for port in new_ports:
                actions.append(
                    Action(
                        tool="alert",
                        action="new_listener",
                        params={"port": port},
                        description=f"New listening port detected: {port}",
                    )
                )

        # Update known listeners
        self._known_listeners = current_ports.copy()

        # ── Tier 2: Connection rate anomaly ──────────────────────

        established = observation.get("established", 0)
        if self._baseline_rate > 0:
            if established > self._baseline_rate * self._rate_threshold:
                actions.append(
                    Action(
                        tool="alert",
                        action="connection_spike",
                        params={
                            "current": established,
                            "baseline": round(self._baseline_rate, 1),
                            "multiplier": round(established / self._baseline_rate, 1),
                        },
                        description=f"Connection spike: {established} (baseline {self._baseline_rate:.0f})",
                    )
                )

        # Update baseline (EWMA)
        alpha = 0.1
        if self._baseline_rate == 0:
            self._baseline_rate = float(established)
        else:
            self._baseline_rate = alpha * established + (1 - alpha) * self._baseline_rate

        # ── Tier 3: LLM analysis ─────────────────────────────────

        if self._llm_enabled and self.model_bus and actions:
            anomaly_details = [a.description for a in actions]
            actions.append(
                Action(
                    tool="model_bus",
                    action="analyze_network",
                    params={
                        "anomalies": anomaly_details,
                        "established": established,
                        "listening_ports": observation.get("listening_ports", []),
                    },
                    description="LLM network anomaly analysis",
                )
            )

        self._first_cycle = False
        return actions

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute network alerts and analysis."""
        results: list[ActionResult] = []

        for action in actions:
            start = time.monotonic()

            if action.tool == "alert":
                self._log.warning(
                    f"net_agent.{action.action}",
                    **{k: v for k, v in action.params.items() if isinstance(v, (int, float, str))},
                )
                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        output=action.description,
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                )

            elif action.tool == "model_bus" and self.model_bus:
                try:
                    anomalies_text = "\n".join(action.params.get("anomalies", []))
                    prompt = (
                        f"Network anomaly detected on developer workstation.\n"
                        f"Current established connections: {action.params.get('established', '?')}\n"
                        f"Listening ports: {action.params.get('listening_ports', [])}\n"
                        f"Anomalies:\n{anomalies_text}\n\n"
                        f"Is this normal developer activity or suspicious? Explain."
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
                    results.append(
                        ActionResult(
                            action=action,
                            success=True,
                            output=analysis[:1000],
                            duration_ms=(time.monotonic() - start) * 1000,
                        )
                    )
                except Exception as e:
                    results.append(
                        ActionResult(
                            action=action,
                            success=False,
                            error=str(e),
                            duration_ms=(time.monotonic() - start) * 1000,
                        )
                    )
            else:
                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        output="No handler",
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                )

        return results
