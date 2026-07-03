"""Managed exit path: breakeven -> partial take-profit -> trailing runner.

Opt-in alternative to the default bracket-order exit (``settings.managed_exits_enabled``).
Each managed position is a market entry plus a single full-qty stop order that
gets replaced in place as the trade matures:

  * R < breakeven_r:   stop stays at the original ATR-based stop.
  * R >= breakeven_r:  stop moves to entry (stage="breakeven").
  * R >= partial_tp_r: sell half the remaining qty at market, tighten stop
                        on the rest (stage="partial").
  * stop trails by ``exit_trail_atr_mult`` * ATR, never loosening.
  * R >= runner_tp_r:  close the remainder at market (stage="closed").
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from ..brokers.base import Broker, BrokerError
from ..config import settings
from ..db import session_scope
from ..models import ManagedPosition
from . import audit as audit_log

log = logging.getLogger("exits")

Broadcast = Callable[[str, dict], Awaitable[None]]


async def open_managed(broker: Broker, db: Session, *, symbol: str, direction: int,
                       qty: int, entry_price: float, r_unit: float) -> ManagedPosition:
    """Submit a market entry plus a full-qty protective stop, track it as managed."""
    side = "buy" if direction > 0 else "sell"
    stop_side = "sell" if direction > 0 else "buy"
    stop_price = entry_price - r_unit * direction

    await asyncio.to_thread(
        broker.place_order, symbol=symbol, side=side, qty=qty, type="market",
        client_order_id=f"entry-{uuid.uuid4().hex[:12]}",
    )
    stop = await asyncio.to_thread(
        broker.place_order, symbol=symbol, side=stop_side, qty=qty, type="stop",
        stop_loss=stop_price, client_order_id=f"stop-{uuid.uuid4().hex[:12]}",
    )

    pos = ManagedPosition(
        symbol=symbol, mode=broker.mode, direction=direction,
        entry_price=entry_price, qty_total=qty, qty_remaining=qty,
        stop_order_id=stop.broker_order_id, r_unit=r_unit, stage="open",
    )
    db.add(pos)
    db.flush()
    audit_log.write("exit_opened", f"managed entry {symbol} qty={qty} @ {entry_price}",
                    actor="engine", db=db,
                    detail={"symbol": symbol, "direction": direction, "qty": qty})
    return pos


def _tighter(direction: int, current: float, candidate: float) -> float:
    """Never loosen a stop: for longs the stop can only rise, for shorts only fall."""
    return max(current, candidate) if direction > 0 else min(current, candidate)


async def manage_open_positions(broker: Broker, hub_broadcast: Broadcast | None = None) -> None:
    """Advance every open managed position by one stage-check tick."""
    with session_scope() as db:
        open_positions = (
            db.query(ManagedPosition)
            .filter(ManagedPosition.stage != "closed")
            .all()
        )
        if not open_positions:
            return

        try:
            open_orders = {o["id"]: o for o in await asyncio.to_thread(broker.get_open_orders)}
        except BrokerError as e:
            log.error("manage_open_positions: get_open_orders failed: %s", e)
            return

        for pos in open_positions:
            if pos.stop_order_id not in open_orders:
                # Stop is no longer open -> filled (or cancelled) -> position closed.
                pos.qty_remaining = 0
                pos.stage = "closed"
                audit_log.write("exit_closed", f"{pos.symbol} stop filled/closed",
                                actor="engine", db=db, detail={"symbol": pos.symbol})
                if hub_broadcast:
                    await hub_broadcast("exit", {"symbol": pos.symbol, "stage": "closed"})
                continue

            try:
                bars = await asyncio.to_thread(broker.get_bars, pos.symbol, lookback_minutes=60)
            except BrokerError as e:
                log.error("manage_open_positions: get_bars failed for %s: %s", pos.symbol, e)
                continue
            if bars.empty:
                continue

            from tradebot.indicators import atr as atr_fn
            price = float(bars["close"].iloc[-1])
            current_atr = float(atr_fn(bars["high"], bars["low"], bars["close"]).iloc[-1])
            r = (price - pos.entry_price) / pos.r_unit * pos.direction

            stop_order = open_orders[pos.stop_order_id]
            current_stop = float(stop_order["stop_price"] or pos.entry_price)
            new_stage = pos.stage
            new_stop = current_stop

            if r >= settings.partial_tp_r and pos.stage in ("open", "breakeven"):
                half = math.floor(pos.qty_remaining / 2)
                if half > 0:
                    close_side = "sell" if pos.direction > 0 else "buy"
                    await asyncio.to_thread(
                        broker.place_order, symbol=pos.symbol, side=close_side, qty=half,
                        type="market", client_order_id=f"partial-{uuid.uuid4().hex[:12]}",
                    )
                    pos.qty_remaining -= half
                    await asyncio.to_thread(broker.replace_order, pos.stop_order_id,
                                            qty=pos.qty_remaining)
                new_stage = "partial"
                new_stop = pos.entry_price
            elif r >= settings.breakeven_r and pos.stage == "open":
                new_stage = "breakeven"
                new_stop = pos.entry_price

            trail_candidate = price - settings.exit_trail_atr_mult * current_atr * pos.direction
            new_stop = _tighter(pos.direction, new_stop, trail_candidate)

            if r >= settings.runner_tp_r and pos.qty_remaining > 0:
                close_side = "sell" if pos.direction > 0 else "buy"
                await asyncio.to_thread(
                    broker.place_order, symbol=pos.symbol, side=close_side,
                    qty=pos.qty_remaining, type="market",
                    client_order_id=f"runner-{uuid.uuid4().hex[:12]}",
                )
                await asyncio.to_thread(broker.cancel_order, pos.stop_order_id)
                pos.qty_remaining = 0
                new_stage = "closed"

            if new_stage != pos.stage or new_stop != current_stop:
                if new_stage != "closed":
                    await asyncio.to_thread(broker.replace_order, pos.stop_order_id,
                                            stop_price=new_stop)
                audit_log.write(
                    "exit_stage", f"{pos.symbol} -> {new_stage} (R={r:.2f})",
                    actor="engine", db=db,
                    detail={"symbol": pos.symbol, "stage": new_stage, "r": r},
                )
                if hub_broadcast:
                    await hub_broadcast("exit", {"symbol": pos.symbol, "stage": new_stage,
                                                 "r": r, "stop": new_stop})
                pos.stage = new_stage
            db.flush()
