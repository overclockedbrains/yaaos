#!/usr/bin/env bash
# dev-setup.sh — Set up YAAOS development environment.
#
# Creates config directories, symlinks tool manifests, and copies
# example configs so all three daemons (SFS, Model Bus, SystemAgentd)
# work out of the box in dev.
#
# Usage:
#   ./scripts/dev-setup.sh          # Run from repo root
#   ./scripts/dev-setup.sh --force  # Overwrite existing symlinks/configs

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=false

if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

info()  { echo -e "\033[0;36m[dev-setup]\033[0m $*"; }
warn()  { echo -e "\033[0;33m[dev-setup]\033[0m $*"; }
ok()    { echo -e "\033[0;32m[dev-setup]\033[0m $*"; }
skip()  { echo -e "\033[0;90m[dev-setup]\033[0m $* (already exists, use --force to overwrite)"; }

# ── Config directories ────────────────────────────────────────

info "Creating config directories..."
mkdir -p ~/.config/yaaos
mkdir -p ~/.local/run/yaaos

# ── Tool Registry symlink ─────────────────────────────────────
# Symlink repo tools.d → ~/.config/yaaos/tools.d so the Tool Registry
# discovers manifests without needing a system install.

TOOLS_SRC="$REPO_ROOT/src/yaaos-agentd/tools.d"
TOOLS_DST="$HOME/.config/yaaos/tools.d"

if [[ -L "$TOOLS_DST" ]]; then
    CURRENT_TARGET="$(readlink -f "$TOOLS_DST")"
    if [[ "$CURRENT_TARGET" == "$(readlink -f "$TOOLS_SRC")" ]]; then
        ok "tools.d symlink already correct → $TOOLS_SRC"
    elif $FORCE; then
        rm "$TOOLS_DST"
        ln -s "$TOOLS_SRC" "$TOOLS_DST"
        ok "tools.d symlink updated → $TOOLS_SRC"
    else
        skip "tools.d symlink exists but points elsewhere: $CURRENT_TARGET"
    fi
elif [[ -d "$TOOLS_DST" ]]; then
    if $FORCE; then
        rm -rf "$TOOLS_DST"
        ln -s "$TOOLS_SRC" "$TOOLS_DST"
        ok "tools.d replaced with symlink → $TOOLS_SRC"
    else
        skip "tools.d is a real directory at $TOOLS_DST"
    fi
else
    ln -s "$TOOLS_SRC" "$TOOLS_DST"
    ok "tools.d symlinked → $TOOLS_SRC"
fi

# ── Example config ────────────────────────────────────────────
# Copy agentd.toml.example → ~/.config/yaaos/agentd.toml if no config exists.

AGENTD_CONFIG="$HOME/.config/yaaos/agentd.toml"
AGENTD_EXAMPLE="$REPO_ROOT/src/yaaos-agentd/agentd.toml.example"

if [[ -f "$AGENTD_CONFIG" ]]; then
    if $FORCE; then
        cp "$AGENTD_EXAMPLE" "$AGENTD_CONFIG"
        ok "agentd.toml overwritten from example"
    else
        skip "agentd.toml already exists at $AGENTD_CONFIG"
    fi
else
    cp "$AGENTD_EXAMPLE" "$AGENTD_CONFIG"
    ok "agentd.toml created from example → $AGENTD_CONFIG"
fi

# ── Summary ───────────────────────────────────────────────────

echo ""
info "Dev environment ready. Verify with:"
echo "  uv run systemagentd          # Start supervisor daemon"
echo "  uv run systemagentctl status  # Check agents"
echo "  uv run systemagentctl tools list  # Should show 9 tools"
