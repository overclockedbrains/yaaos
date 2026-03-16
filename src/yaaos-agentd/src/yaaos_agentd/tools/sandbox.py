"""Bubblewrap integration for sandboxed tool execution.

Provides lightweight namespace isolation for CLI tools using
bubblewrap (bwrap). Different tools get different isolation levels
based on their declared sandbox tier.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum


class SandboxTier(Enum):
    """Sandbox isolation levels for tools."""

    NONE = "none"               # Trusted system tools (systemctl, journalctl)
    READ_ONLY = "read-only"     # Read-only tools (git status, docker ps)
    NETWORK_ISOLATED = "network-isolated"  # Build tools, no network
    FULL = "full"               # Untrusted tools, maximum isolation


@dataclass
class SandboxPolicy:
    """Configuration for a tool's sandbox environment."""

    tier: SandboxTier = SandboxTier.NONE
    allowed_paths: list[str] = field(default_factory=list)
    network: bool = True
    working_dir: str = "/tmp"

    def to_bwrap_args(self) -> list[str]:
        """Build bubblewrap command-line arguments for this policy.

        Returns empty list if bwrap is not available or tier is NONE.
        """
        if self.tier == SandboxTier.NONE:
            return []

        if not shutil.which("bwrap"):
            return []

        args = ["bwrap"]

        if self.tier == SandboxTier.READ_ONLY:
            args.extend(["--ro-bind", "/", "/"])
            # Allow writes to working dir
            args.extend(["--bind", self.working_dir, self.working_dir])
            args.extend(["--bind", "/tmp", "/tmp"])
            # Honor explicit network=false even in read-only tier
            if not self.network:
                args.append("--unshare-net")

        elif self.tier == SandboxTier.NETWORK_ISOLATED:
            args.extend(["--ro-bind", "/", "/"])
            args.extend(["--bind", self.working_dir, self.working_dir])
            args.extend(["--bind", "/tmp", "/tmp"])
            args.append("--unshare-net")

        elif self.tier == SandboxTier.FULL:
            # Minimal binds
            args.extend(["--ro-bind", "/usr", "/usr"])
            args.extend(["--ro-bind", "/lib", "/lib"])
            args.extend(["--ro-bind", "/lib64", "/lib64"])
            args.extend(["--ro-bind", "/bin", "/bin"])
            args.extend(["--ro-bind", "/sbin", "/sbin"])
            args.extend(["--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf"])
            args.extend(["--tmpfs", "/tmp"])
            args.extend(["--proc", "/proc"])
            args.extend(["--dev", "/dev"])
            args.append("--unshare-all")

        # Add explicitly allowed paths
        for path in self.allowed_paths:
            args.extend(["--bind", path, path])

        # Common settings
        args.extend(["--die-with-parent"])
        args.extend(["--chdir", self.working_dir])

        # Separator between bwrap args and the actual command
        args.append("--")

        return args


def sandbox_from_config(config: dict | None) -> SandboxPolicy:
    """Create a SandboxPolicy from a tool manifest's [tool.sandbox] section."""
    if config is None:
        return SandboxPolicy(tier=SandboxTier.NONE)

    tier_str = config.get("tier", "none")
    try:
        tier = SandboxTier(tier_str)
    except ValueError:
        tier = SandboxTier.NONE

    return SandboxPolicy(
        tier=tier,
        allowed_paths=config.get("allowed_paths", []),
        network=config.get("network", True),
        working_dir=config.get("working_dir", "/tmp"),
    )
