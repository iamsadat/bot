"""Order placement & cancellation routes."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..brokers.base import BrokerError, NotConfiguredError
from ..db import get_db
from ..deps import make_broker
from ..models import OrderRecord
from ..schemas import CancelOrderRequest, ManualOrderRequest, OrderInfo
from ..trading import audit as audit_log
from ..trading import state as state_mod
from ..trading.risk import assess_order
from ..ws import hub

router = APIRouter()


@router.get("/orders/open")
def list_open_orders():
    broker = make_broker()
    try:
        return broker.get_open_orders()
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/orders", response_model=list[OrderInfo])
def list_recorded_orders(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(OrderRecord).order_by(desc(OrderRecord.ts)).limit(limit).all()
    return [
        OrderInfo(
            id=r.id, ts=r.ts, symbol=r.symbol, side=r.side, qty=r.qty,
            type=r.type, status=r.status, broker_order_id=r.broker_order_id,
            source=r.source, limit_price=r.limit_price, stop_price=r.stop_price,
            take_profit=r.take_profit, rejected_reason=r.rejected_reason,
        )
        for r in rows
    ]


@router.post("/orders", response_model=OrderInfo)
async def place_manual_order(req: ManualOrderRequest,
                             db: Session = Depends(get_db)):
    state = state_mod.get_or_create(db)
    if state.kill_switch:
        raise HTTPException(status_code=423, detail="kill_switch_active")
    if state.halted_today:
        raise HTTPException(status_code=423, detail="daily_halt_active")

    broker = make_broker()
    try:
        account = broker.get_account()
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Use the limit price for risk math when present; otherwise the latest mark.
    ref_price = req.limit_price
    if ref_price is None:
        try:
            bars = broker.get_bars(req.symbol, lookback_minutes=5)
            ref_price = float(bars["close"].iloc[-1]) if len(bars) else None
        except BrokerError:
            ref_price = None
    if ref_price is None or ref_price <= 0:
        raise HTTPException(status_code=400,
                            detail="cannot determine reference price")

    verdict, reason = assess_order(
        kill_switch=state.kill_switch,
        halted_today=state.halted_today,
        qty=req.qty,
        price=ref_price,
        account=account,
    )
    if verdict != "ok":
        rec = OrderRecord(
            idempotency_key=req.idempotency_key or f"reject-{uuid.uuid4().hex[:12]}",
            broker=broker.name, mode=broker.mode, symbol=req.symbol,
            side=req.side, qty=req.qty, type=req.type, source="manual",
            status="rejected", rejected_reason=reason,
            limit_price=req.limit_price, stop_price=req.stop_loss,
            take_profit=req.take_profit,
        )
        db.add(rec)
        audit_log.write("order_rejected",
                        f"manual: {req.side} {req.qty} {req.symbol} ({reason})",
                        actor="user", detail={"reason": reason}, db=db)
        db.commit()
        raise HTTPException(status_code=400, detail=reason)

    idem = req.idempotency_key or f"manual-{uuid.uuid4().hex[:12]}"
    existing = db.query(OrderRecord).filter_by(idempotency_key=idem).first()
    if existing is not None:
        return OrderInfo(
            id=existing.id, ts=existing.ts, symbol=existing.symbol,
            side=existing.side, qty=existing.qty, type=existing.type,
            status=existing.status, broker_order_id=existing.broker_order_id,
            source=existing.source, limit_price=existing.limit_price,
            stop_price=existing.stop_price, take_profit=existing.take_profit,
            rejected_reason=existing.rejected_reason,
        )

    try:
        res = broker.place_order(
            symbol=req.symbol, side=req.side, qty=req.qty, type=req.type,
            limit_price=req.limit_price, stop_loss=req.stop_loss,
            take_profit=req.take_profit, time_in_force=req.time_in_force,
            client_order_id=idem,
        )
    except BrokerError as e:
        rec = OrderRecord(
            idempotency_key=idem, broker=broker.name, mode=broker.mode,
            symbol=req.symbol, side=req.side, qty=req.qty, type=req.type,
            source="manual", status="error", rejected_reason=str(e),
            limit_price=req.limit_price, stop_price=req.stop_loss,
            take_profit=req.take_profit,
        )
        db.add(rec)
        audit_log.write("order_error",
                        f"manual: {req.side} {req.qty} {req.symbol} — {e}",
                        actor="user", detail={"error": str(e)}, db=db)
        db.commit()
        raise HTTPException(status_code=502, detail=str(e))

    rec = OrderRecord(
        idempotency_key=idem, broker=broker.name, mode=broker.mode,
        symbol=req.symbol, side=req.side, qty=req.qty, type=req.type,
        source="manual", status=res.status,
        broker_order_id=res.broker_order_id,
        limit_price=req.limit_price, stop_price=req.stop_loss,
        take_profit=req.take_profit,
    )
    db.add(rec)
    audit_log.write(
        "order_submitted",
        f"manual: {req.side} {req.qty} {req.symbol} ({req.type})",
        actor="user",
        detail={"broker_order_id": res.broker_order_id}, db=db,
    )
    db.commit()
    db.refresh(rec)
    await hub.broadcast("order", {"symbol": req.symbol, "side": req.side,
                                  "qty": req.qty, "source": "manual",
                                  "broker_order_id": res.broker_order_id})
    return OrderInfo(
        id=rec.id, ts=rec.ts, symbol=rec.symbol, side=rec.side, qty=rec.qty,
        type=rec.type, status=rec.status, broker_order_id=rec.broker_order_id,
        source=rec.source, limit_price=rec.limit_price,
        stop_price=rec.stop_price, take_profit=rec.take_profit,
        rejected_reason=rec.rejected_reason,
    )


@router.post("/orders/cancel")
def cancel(req: CancelOrderRequest, db: Session = Depends(get_db)):
    broker = make_broker()
    try:
        if req.cancel_all:
            n = broker.cancel_all()
            audit_log.write("cancel_all",
                            f"cancelled {n} open orders",
                            actor="user", db=db)
            db.commit()
            return {"cancelled": n}
        if not req.broker_order_id:
            raise HTTPException(status_code=400,
                                detail="broker_order_id required")
        broker.cancel_order(req.broker_order_id)
        audit_log.write("order_cancel",
                        f"cancelled {req.broker_order_id}",
                        actor="user", db=db)
        db.commit()
        return {"cancelled": 1, "broker_order_id": req.broker_order_id}
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/positions/{symbol}/close")
def close_position(symbol: str, db: Session = Depends(get_db)):
    broker = make_broker()
    try:
        broker.close_position(symbol.upper())
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit_log.write("close_position", f"closed {symbol.upper()}",
                    actor="user", db=db)
    db.commit()
    return {"closed": symbol.upper()}
