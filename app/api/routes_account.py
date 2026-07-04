"""Account, positions, market-data routes."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, HTTPException

from ..brokers.base import BrokerError, NotConfiguredError
from ..deps import make_broker
from ..schemas import AccountInfo, Bar, BarsResponse, PositionInfo

router = APIRouter()


@router.get("/account", response_model=AccountInfo)
def get_account():
    broker = make_broker()
    try:
        snap = broker.get_account()
        market_open = broker.is_market_open()
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return AccountInfo(
        broker=broker.name,
        mode=broker.mode,
        cash=snap.cash,
        equity=snap.equity,
        buying_power=snap.buying_power,
        portfolio_value=snap.portfolio_value,
        day_pnl=snap.day_pnl,
        day_pnl_pct=snap.day_pnl_pct,
        is_market_open=market_open,
    )


@router.get("/positions", response_model=list[PositionInfo])
def get_positions():
    broker = make_broker()
    try:
        ps = broker.get_positions()
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [
        PositionInfo(
            symbol=p.symbol, qty=p.qty,
            avg_entry_price=p.avg_entry_price,
            market_price=p.market_price,
            market_value=p.market_value,
            unrealized_pnl=p.unrealized_pnl,
            unrealized_pnl_pct=p.unrealized_pnl_pct,
            side=p.side,
        )
        for p in ps
    ]


@router.get("/account/history")
def get_account_history(period: Literal["1D", "1W", "1M"] = "1M"):
    broker = make_broker()
    try:
        hist = broker.get_portfolio_history(period)
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"timestamps": hist.timestamps, "equity": hist.equity,
            "profit_loss": hist.profit_loss}


def _build_trades(closed_orders: list[dict]) -> list[dict]:
    """Pair opening/closing fills per symbol (FIFO) into round-trip trades.

    ponytail: FIFO-per-symbol over the fetched order window; good enough for
    a single-account paper/live book. Upgrade to lot-level broker data if
    partial fills ever need to be reconciled exactly.
    """
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for o in closed_orders:
        by_symbol[o["symbol"]].append(o)

    trades: list[dict] = []
    for symbol, orders in by_symbol.items():
        orders.sort(key=lambda o: o["filled_at"])
        open_stack: deque[dict] = deque()
        for o in orders:
            side, qty, price, ts = o["side"], o["qty"], o["filled_avg_price"], o["filled_at"]
            if not open_stack or open_stack[0]["side"] == side:
                open_stack.append({"side": side, "qty": qty, "price": price, "ts": ts})
                continue
            remaining = qty
            while remaining > 1e-9 and open_stack:
                entry = open_stack[0]
                matched = min(entry["qty"], remaining)
                direction = 1 if entry["side"] == "buy" else -1
                pnl = (price - entry["price"]) * matched * direction
                pnl_pct = (price / entry["price"] - 1) * direction if entry["price"] else 0.0
                trades.append({
                    "symbol": symbol,
                    "direction": "long" if direction > 0 else "short",
                    "qty": matched,
                    "entry_price": entry["price"],
                    "exit_price": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "opened_at": entry["ts"],
                    "closed_at": ts,
                })
                entry["qty"] -= matched
                remaining -= matched
                if entry["qty"] <= 1e-9:
                    open_stack.popleft()
            if remaining > 1e-9:
                # More closing qty than tracked open qty (position predates
                # our fetch window) -> treat the excess as a fresh open.
                open_stack.append({"side": side, "qty": remaining, "price": price, "ts": ts})
    trades.sort(key=lambda t: t["closed_at"], reverse=True)
    return trades


@router.get("/trades")
def get_trades(limit: int = 500):
    broker = make_broker()
    try:
        closed = broker.get_closed_orders(limit=limit)
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"trades": _build_trades(closed)}


@router.get("/market/bars/{symbol}", response_model=BarsResponse)
def get_bars(symbol: str, lookback_minutes: int = 240):
    broker = make_broker()
    try:
        df = broker.get_bars(symbol.upper(), lookback_minutes=lookback_minutes)
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    bars = [
        Bar(ts=idx, open=row.open, high=row.high, low=row.low,
            close=row.close, volume=row.volume)
        for idx, row in df.iterrows()
    ]
    return BarsResponse(symbol=symbol.upper(), bars=bars)
