"""FS-Agent — SFS daemon wrapper.

Manages the existing Semantic File System (SFS) daemon as a supervised
agent under SystemAgentd. Uses dbus-next to monitor and control the
yaaos-sfs.service systemd unit.

Observe: Check SFS service status via D-Bus
Reason:  Detect if SFS is down/degraded, decide whether to restart
Act:     Start/restart SFS via systemd, report status
"""

from __future__ import annotations

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.types import Action, ActionResult, AgentSpec


class FsAgent(BaseAgent):
    """Wraps the SFS daemon as a managed agent under SystemAgentd.

    Config:
        delegate_to: systemd unit name (default: yaaos-sfs.service)
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self._unit_name = spec.config.get("delegate_to", "yaaos-sfs.service")
        self._systemd = None
        self._consecutive_failures = 0

    async def on_start(self) -> None:
        """Connect to D-Bus for systemd management."""
        from yaaos_agentd.systemd import SystemdManager

        self._systemd = SystemdManager()
        try:
            await self._systemd.connect()
            self._log.info("fs_agent.connected", unit=self._unit_name)
        except Exception as e:
            self._log.warning(
                "fs_agent.dbus_unavailable",
                error=str(e),
                hint="Running without systemd control — status-only mode",
            )
            self._systemd = None

    async def on_stop(self) -> None:
        """Disconnect from D-Bus."""
        if self._systemd:
            await self._systemd.disconnect()

    async def observe(self) -> dict:
        """Check SFS service status via systemd D-Bus."""
        if not self._systemd or not self._systemd.is_connected:
            return {"status": "no_dbus", "unit": self._unit_name}

        try:
            unit_status = await self._systemd.unit_status(self._unit_name)
            return {
                "status": "ok",
                "unit": self._unit_name,
                "active_state": unit_status.active_state,
                "sub_state": unit_status.sub_state,
                "main_pid": unit_status.main_pid,
                "memory_bytes": unit_status.memory_bytes,
            }
        except Exception as e:
            return {"status": "error", "unit": self._unit_name, "error": str(e)}

    async def reason(self, observation: dict) -> list[Action]:
        """Decide actions based on SFS service state."""
        if observation.get("status") == "no_dbus":
            return []

        if observation.get("status") == "error":
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._log.warning(
                    "fs_agent.persistent_dbus_error", failures=self._consecutive_failures
                )
            return []

        self._consecutive_failures = 0
        active_state = observation.get("active_state", "unknown")

        if active_state == "failed":
            self._log.warning("fs_agent.sfs_failed", unit=self._unit_name)
            return [
                Action(
                    tool="systemd",
                    action="restart",
                    params={"unit": self._unit_name},
                    description=f"Restart failed SFS service {self._unit_name}",
                )
            ]

        if active_state == "inactive":
            self._log.info("fs_agent.sfs_inactive", unit=self._unit_name)
            return [
                Action(
                    tool="systemd",
                    action="start",
                    params={"unit": self._unit_name},
                    description=f"Start inactive SFS service {self._unit_name}",
                )
            ]

        return []

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute systemd actions on the SFS service."""
        results = []
        for action in actions:
            if not self._systemd or not self._systemd.is_connected:
                results.append(
                    ActionResult(
                        action=action,
                        success=False,
                        error="D-Bus not connected",
                    )
                )
                continue

            try:
                unit = action.params.get("unit", self._unit_name)
                if action.action == "start":
                    await self._systemd.start_unit(unit)
                elif action.action == "restart":
                    await self._systemd.restart_unit(unit)
                elif action.action == "stop":
                    await self._systemd.stop_unit(unit)

                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        output=f"{action.action} {unit} succeeded",
                    )
                )
            except Exception as e:
                self._log.error("fs_agent.action_failed", action=action.action, error=str(e))
                results.append(
                    ActionResult(
                        action=action,
                        success=False,
                        error=str(e),
                    )
                )

        return results
