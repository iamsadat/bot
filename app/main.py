"""FastAPI application entry point.

Run locally:

    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

The frontend (Vite dev server on :5173) is allowed via CORS by default.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import (
    routes_account,
    routes_audit,
    routes_orders,
    routes_safety,
    routes_strategy,
)
from .config import settings
from .db import init_db
from .deps import make_broker
from .trading.engine import TradingEngine
from .ws import hub


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    engine = TradingEngine(broker_factory=make_broker, broadcast=hub.broadcast)
    app.state.engine = engine
    try:
        yield
    finally:
        try:
            await engine.stop()
        except Exception:                                       # noqa: BLE001
            pass


app = FastAPI(
    title="TradeBot API",
    version="0.1.0",
    description="Paper-first automated + manual trading on top of Alpaca.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_account.router, prefix="/api", tags=["account"])
app.include_router(routes_orders.router, prefix="/api", tags=["orders"])
app.include_router(routes_strategy.router, prefix="/api", tags=["strategy"])
app.include_router(routes_safety.router, prefix="/api", tags=["safety"])
app.include_router(routes_audit.router, prefix="/api", tags=["audit"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    await hub.add(ws)
    try:
        while True:
            # We don't expect inbound messages; just keep the socket alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)


# Optional: serve the built frontend if it has been compiled.
import os
_static = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
