"""WebSocket broadcast hub.

A single global hub holds the set of connected clients.  Server events
(decisions, orders, kill-switch, mode changes) are broadcast via
``hub.broadcast(kind, payload)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("ws")


class Hub:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, kind: str, payload: Any) -> None:
        msg = json.dumps({"kind": kind, "payload": payload}, default=str)
        async with self._lock:
            dead = []
            for ws in self._clients:
                try:
                    await ws.send_text(msg)
                except Exception:                                # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)


hub = Hub()
