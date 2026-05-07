"""Account, positions, market-data routes."""

from __future__ import annotations

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
