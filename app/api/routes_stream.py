"""Live market-data stream control."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..trading import state as state_mod

router = APIRouter()


class SubscribeRequest(BaseModel):
    symbols: list[str]
    mode: Literal["paper", "live"] | None = None


@router.get("/market/subscriptions")
def get_subscriptions(request: Request):
    stream = request.app.state.market_stream
    return {"subscribed": stream.subscriptions}


@router.post("/market/subscribe")
async def subscribe(req: SubscribeRequest, request: Request,
                    db: Session = Depends(get_db)):
    mode = req.mode or state_mod.get_or_create(db).mode
    stream = request.app.state.market_stream
    await stream.ensure_subscribed(req.symbols, mode=mode)
    return {"subscribed": stream.subscriptions, "mode": mode}


@router.post("/market/unsubscribe")
async def unsubscribe(request: Request):
    stream = request.app.state.market_stream
    await stream.stop()
    return {"subscribed": []}
