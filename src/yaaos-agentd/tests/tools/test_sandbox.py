"""Tests for bubblewrap sandbox integration."""

from __future__ import annotations

from yaaos_agentd.tools.sandbox import SandboxPolicy, SandboxTier, sandbox_from_config


class TestSandboxPolicy:
    def test_none_tier_returns_empty(self):
        policy = SandboxPolicy(tier=SandboxTier.NONE)
        assert policy.to_bwrap_args() == []

    def test_read_only_tier(self):
        policy = SandboxPolicy(tier=SandboxTier.READ_ONLY)
        args = policy.to_bwrap_args()
        # If bwrap is not installed, returns empty
        if args:
            assert "bwrap" in args[0]
            assert "--ro-bind" in args

    def test_network_isolated_tier(self):
        policy = SandboxPolicy(tier=SandboxTier.NETWORK_ISOLATED)
        args = policy.to_bwrap_args()
        if args:
            assert "--unshare-net" in args

    def test_full_isolation_tier(self):
        policy = SandboxPolicy(tier=SandboxTier.FULL)
        args = policy.to_bwrap_args()
        if args:
            assert "--unshare-all" in args

    def test_allowed_paths(self):
        policy = SandboxPolicy(
            tier=SandboxTier.READ_ONLY,
            allowed_paths=["/var/run/docker.sock"],
        )
        args = policy.to_bwrap_args()
        if args:
            assert "/var/run/docker.sock" in args


class TestSandboxFromConfig:
    def test_none_config(self):
        policy = sandbox_from_config(None)
        assert policy.tier == SandboxTier.NONE

    def test_read_only_config(self):
        config = {"tier": "read-only", "allowed_paths": ["/tmp"]}
        policy = sandbox_from_config(config)
        assert policy.tier == SandboxTier.READ_ONLY
        assert "/tmp" in policy.allowed_paths

    def test_unknown_tier_defaults_to_none(self):
        config = {"tier": "unknown"}
        policy = sandbox_from_config(config)
        assert policy.tier == SandboxTier.NONE
