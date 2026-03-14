"""Daemon client — connects to the running daemon for fast queries."""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass

from .search import SearchResult

_HEADER = struct.Struct("!I")
_TIMEOUT = 5.0  # seconds


class DaemonNotRunning(Exception):
    pass


class DaemonClient:
    """Client that talks to the daemon's query server."""

    def __init__(self, port: int = 9749):
        self.port = port

    def _request(self, msg: dict) -> dict:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_TIMEOUT)
        try:
            sock.connect(("127.0.0.1", self.port))

            # Send
            payload = json.dumps(msg).encode()
            sock.sendall(_HEADER.pack(len(payload)) + payload)

            # Receive header
            header = b""
            while len(header) < _HEADER.size:
                chunk = sock.recv(_HEADER.size - len(header))
                if not chunk:
                    raise DaemonNotRunning("Connection closed")
                header += chunk

            (length,) = _HEADER.unpack(header)

            # Receive body
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    raise DaemonNotRunning("Connection closed")
                data += chunk

            return json.loads(data)
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError, OSError) as e:
            raise DaemonNotRunning(f"Daemon not running on port {self.port}: {e}")
        finally:
            sock.close()

    def ping(self) -> bool:
        try:
            resp = self._request({"type": "ping"})
            return resp.get("ok", False)
        except DaemonNotRunning:
            return False

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        resp = self._request({"type": "search", "query": query, "top_k": top_k})

        if "error" in resp:
            raise RuntimeError(resp["error"])

        return [
            SearchResult(
                file_path=r["file_path"],
                filename=r["filename"],
                chunk_text=r["chunk_text"],
                chunk_index=r["chunk_index"],
                score=r["score"],
            )
            for r in resp["results"]
        ]

    def status(self) -> dict:
        resp = self._request({"type": "status"})
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp
