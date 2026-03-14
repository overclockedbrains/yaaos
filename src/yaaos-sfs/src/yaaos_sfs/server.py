"""Query server — runs inside the daemon, serves search requests over TCP."""

from __future__ import annotations

import json
import logging
import socketserver
import struct
import threading
from dataclasses import asdict

from .config import Config
from .db import Database
from .providers import EmbeddingProvider
from .search import hybrid_search

log = logging.getLogger("yaaos-sfs")

# Wire format: 4-byte big-endian length prefix + JSON payload
_HEADER = struct.Struct("!I")


def _recv_msg(sock) -> dict | None:
    header = b""
    while len(header) < _HEADER.size:
        chunk = sock.recv(_HEADER.size - len(header))
        if not chunk:
            return None
        header += chunk

    (length,) = _HEADER.unpack(header)
    if length > 10 * 1024 * 1024:  # 10 MB sanity limit
        return None

    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk

    return json.loads(data)


def _send_msg(sock, obj: dict):
    payload = json.dumps(obj).encode()
    sock.sendall(_HEADER.pack(len(payload)) + payload)


class _QueryHandler(socketserver.BaseRequestHandler):
    """Handles a single client connection."""

    def handle(self):
        try:
            msg = _recv_msg(self.request)
            if msg is None:
                return

            server: QueryServer = self.server
            response = server.dispatch(msg)
            _send_msg(self.request, response)
        except Exception as e:
            try:
                _send_msg(self.request, {"error": str(e)})
            except Exception:
                pass


class QueryServer(socketserver.ThreadingTCPServer):
    """TCP server that handles search and status queries from yaaos-find."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, db: Database, provider: EmbeddingProvider, config: Config):
        self.db = db
        self.provider = provider
        self.config = config
        super().__init__(("127.0.0.1", config.query_port), _QueryHandler)

    def dispatch(self, msg: dict) -> dict:
        msg_type = msg.get("type")

        if msg_type == "search":
            return self._handle_search(msg)
        elif msg_type == "status":
            return self._handle_status()
        elif msg_type == "ping":
            return {"ok": True}
        else:
            return {"error": f"Unknown message type: {msg_type}"}

    def _handle_search(self, msg: dict) -> dict:
        query = msg.get("query", "")
        top_k = msg.get("top_k", 10)

        if not query:
            return {"error": "Empty query"}

        results = hybrid_search(self.db, self.provider, query, top_k=top_k)

        return {
            "results": [asdict(r) for r in results],
        }

    def _handle_status(self) -> dict:
        stats = self.db.get_stats()
        type_breakdown = self.db.get_stats_by_type()
        return {
            "stats": stats,
            "watch_dir": str(self.config.watch_dir),
            "provider": self.config.embedding_provider,
            "model": self.config.embedding_model,
            "type_breakdown": type_breakdown,
        }

    def start_background(self) -> threading.Thread:
        """Start the server in a background thread."""
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        log.info(f"Query server listening on 127.0.0.1:{self.config.query_port}")
        return thread
