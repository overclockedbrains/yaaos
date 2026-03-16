"""Per-agent SQLite state persistence.

Each agent gets an independent SQLite database for persisting
statistical accumulators, baselines, and analysis history across
restarts. Databases are stored at /var/lib/yaaos/agents/<name>/state.db.

Inspired by LangGraph checkpointing — agents can save/load arbitrary
key-value state without sharing databases (isolation by design).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import orjson
import structlog

logger = structlog.get_logger()

_DEFAULT_STATE_DIR = Path("/var/lib/yaaos/agents")
_FALLBACK_STATE_DIR = Path("~/.local/share/yaaos/agents")


class AgentStateDB:
    """SQLite key-value store for a single agent's persistent state.

    Usage:
        db = AgentStateDB("log")
        db.set("log_rate_baseline", {"mean": 42.5, "stddev": 3.2})
        baseline = db.get("log_rate_baseline")
        db.close()
    """

    def __init__(self, agent_name: str, state_dir: Path | None = None):
        self._agent_name = agent_name
        self._log = logger.bind(component="state_db", agent=agent_name)

        base_dir = state_dir or _resolve_state_dir()
        self._db_dir = base_dir / agent_name
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._db_dir / "state.db"

        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )"""
        )
        self._conn.commit()
        self._log.debug("state_db.opened", path=str(self._db_path))

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key, deserializing from JSON."""
        row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return orjson.loads(row[0])

    def set(self, key: str, value: Any) -> None:
        """Set a key-value pair, serializing to JSON."""
        data = orjson.dumps(value)
        self._conn.execute(
            """INSERT INTO state (key, value, updated_at)
               VALUES (?, ?, strftime('%s', 'now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, data),
        )
        self._conn.commit()

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if the key existed."""
        cursor = self._conn.execute("DELETE FROM state WHERE key = ?", (key,))
        self._conn.commit()
        return cursor.rowcount > 0

    def keys(self) -> list[str]:
        """List all stored keys."""
        rows = self._conn.execute("SELECT key FROM state ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def clear(self) -> None:
        """Delete all state for this agent."""
        self._conn.execute("DELETE FROM state")
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        self._log.debug("state_db.closed")

    def __enter__(self) -> AgentStateDB:
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _resolve_state_dir() -> Path:
    """Find a writable state directory."""
    if _DEFAULT_STATE_DIR.exists() or _can_create(_DEFAULT_STATE_DIR):
        return _DEFAULT_STATE_DIR
    return _FALLBACK_STATE_DIR.expanduser()


def _can_create(path: Path) -> bool:
    """Check if we can create a directory at this path."""
    import os

    parent = path.parent
    try:
        return parent.exists() and os.access(parent, os.W_OK)
    except OSError:
        return False
