"""Async systemd unit management via dbus-next.

Provides a high-level async interface for managing systemd units
(start, stop, restart, status) without blocking the event loop.
Used by FS-Agent and supervisor for delegating to systemd services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class UnitStatus:
    """Status snapshot of a systemd unit."""

    name: str
    load_state: str       # "loaded", "not-found", "masked"
    active_state: str     # "active", "inactive", "failed", "activating"
    sub_state: str        # "running", "dead", "failed", "exited"
    description: str = ""
    main_pid: int = 0
    memory_bytes: int = 0

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"

    @property
    def is_failed(self) -> bool:
        return self.active_state == "failed"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "load_state": self.load_state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
        }
        if self.description:
            d["description"] = self.description
        if self.main_pid:
            d["main_pid"] = self.main_pid
        if self.memory_bytes:
            d["memory_mb"] = round(self.memory_bytes / (1024 * 1024), 1)
        return d


class SystemdManager:
    """Async systemd unit manager using dbus-next.

    Usage:
        mgr = SystemdManager()
        await mgr.connect()
        status = await mgr.unit_status("yaaos-sfs.service")
        await mgr.start_unit("yaaos-sfs.service")
        await mgr.disconnect()
    """

    def __init__(self):
        self._bus = None
        self._manager = None
        self._log = logger.bind(component="systemd_manager")

    async def connect(self) -> None:
        """Connect to the system D-Bus."""
        try:
            from dbus_next.aio import MessageBus
            self._bus = await MessageBus(bus_type=_bus_type()).connect()
            introspection = await self._bus.introspect(
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
            )
            proxy = self._bus.get_proxy_object(
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                introspection,
            )
            self._manager = proxy.get_interface("org.freedesktop.systemd1.Manager")
            self._log.info("systemd_manager.connected")
        except Exception as e:
            self._log.warning("systemd_manager.connect_failed", error=str(e))
            self._bus = None
            self._manager = None

    async def disconnect(self) -> None:
        """Disconnect from D-Bus."""
        if self._bus:
            self._bus.disconnect()
            self._bus = None
            self._manager = None
            self._log.debug("systemd_manager.disconnected")

    @property
    def is_connected(self) -> bool:
        return self._manager is not None

    async def start_unit(self, unit_name: str) -> str:
        """Start a systemd unit. Returns the job path."""
        self._ensure_connected()
        job = await self._manager.call_start_unit(unit_name, "replace")  # type: ignore[union-attr]
        self._log.info("systemd.unit_started", unit=unit_name, job=str(job))
        return str(job)

    async def stop_unit(self, unit_name: str) -> str:
        """Stop a systemd unit. Returns the job path."""
        self._ensure_connected()
        job = await self._manager.call_stop_unit(unit_name, "replace")  # type: ignore[union-attr]
        self._log.info("systemd.unit_stopped", unit=unit_name, job=str(job))
        return str(job)

    async def restart_unit(self, unit_name: str) -> str:
        """Restart a systemd unit. Returns the job path."""
        self._ensure_connected()
        job = await self._manager.call_restart_unit(unit_name, "replace")  # type: ignore[union-attr]
        self._log.info("systemd.unit_restarted", unit=unit_name, job=str(job))
        return str(job)

    async def unit_status(self, unit_name: str) -> UnitStatus:
        """Get the current status of a systemd unit."""
        self._ensure_connected()

        unit_path = await self._manager.call_get_unit(unit_name)  # type: ignore[union-attr]

        introspection = await self._bus.introspect(  # type: ignore[union-attr]
            "org.freedesktop.systemd1", unit_path
        )
        proxy = self._bus.get_proxy_object(  # type: ignore[union-attr]
            "org.freedesktop.systemd1", unit_path, introspection
        )
        props = proxy.get_interface("org.freedesktop.DBus.Properties")

        load_state = await props.call_get("org.freedesktop.systemd1.Unit", "LoadState")
        active_state = await props.call_get("org.freedesktop.systemd1.Unit", "ActiveState")
        sub_state = await props.call_get("org.freedesktop.systemd1.Unit", "SubState")
        description = await props.call_get("org.freedesktop.systemd1.Unit", "Description")

        # Service-specific properties (may not exist for non-service units)
        main_pid = 0
        memory_bytes = 0
        try:
            main_pid = await props.call_get("org.freedesktop.systemd1.Service", "MainPID")
            memory_bytes = await props.call_get(
                "org.freedesktop.systemd1.Service", "MemoryCurrent"
            )
        except Exception:
            pass

        return UnitStatus(
            name=unit_name,
            load_state=_variant_value(load_state),
            active_state=_variant_value(active_state),
            sub_state=_variant_value(sub_state),
            description=_variant_value(description),
            main_pid=_variant_value(main_pid) if main_pid else 0,
            memory_bytes=_variant_value(memory_bytes) if memory_bytes else 0,
        )

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError("SystemdManager not connected — call connect() first")


def _variant_value(variant: Any) -> Any:
    """Extract the value from a dbus-next Variant, or return as-is."""
    if hasattr(variant, "value"):
        return variant.value
    return variant


def _bus_type():
    """Get the dbus-next BusType for system bus."""
    from dbus_next import BusType
    return BusType.SYSTEM
