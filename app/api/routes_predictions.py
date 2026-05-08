"""Live prediction snapshot.

Returns enough information for the frontend to render:

  * the most recent bars annotated with EMAs / VWAP / Bollinger / RSI / MACD
  * the strategy's per-bar score & direction history
  * the *projected* trade plan if a long/short were entered now (so the UI
    can draw the SL and TP lines on the chart).

This re-uses the same indicator and decision functions as the engine, so
the prediction the UI shows is identical to what the engine would act on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tradebot.indicators import compute_all
from tradebot.strategy import StrategyConfig, decide

from ..brokers.base import BrokerError, NotConfiguredError
from ..db import get_db
from ..deps import make_broker
from ..trading import state as state_mod

router = APIRouter()


class IndicatorBar(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema_fast: float | None = None
    ema_mid: float | None = None
    ema_slow: float | None = None
    vwap: float | None = None
    bb_up: float | None = None
    bb_lo: float | None = None
    bb_mid: float | None = None
    rsi: float | None = None
    adx: float | None = None
    atr: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None


class ScoreSample(BaseModel):
    ts: str
    score: float
    direction: int


class TradePlan(BaseModel):
    direction: int
    entry: float
    stop: float
    take_profit: float
    risk_per_share: float
    rr_ratio: float


class PredictionResponse(BaseModel):
    symbol: str
    bars: list[IndicatorBar]
    score_history: list[ScoreSample]
    current_score: float
    current_direction: int
    current_reason: str
    current_votes: dict[str, float]
    plan: TradePlan | None
    last_close: float
    rsi: float
    adx: float
    atr: float
    vwap: float


def _safe(v):
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
    except Exception:                                            # noqa: BLE001
        return None
    return float(v)


@router.get("/prediction/{symbol}", response_model=PredictionResponse)
def get_prediction(symbol: str, lookback_minutes: int = 240,
                   db: Session = Depends(get_db)):
    symbol = symbol.upper()
    broker = make_broker()
    try:
        bars = broker.get_bars(symbol, lookback_minutes=lookback_minutes)
    except NotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if len(bars) < 60:
        raise HTTPException(status_code=400,
                            detail=f"insufficient bars ({len(bars)}) for {symbol}")

    enriched = compute_all(bars)
    cfg = state_mod.get_or_create(db).config or {}
    scfg = StrategyConfig(
        entry_threshold=float(cfg.get("entry_threshold", 0.5)),
        adx_min=float(cfg.get("adx_min", 22.0)),
    )

    # Score history: walk the last ~min(120, len-2) bars, run decide().
    history: list[ScoreSample] = []
    n = len(enriched)
    start = max(2, n - 120)
    for i in range(start, n):
        row = enriched.iloc[i]
        prev = enriched.iloc[i - 1]
        d = decide(row, prev, scfg, bar_in_session=60, bars_per_session=390)
        history.append(ScoreSample(
            ts=str(enriched.index[i]),
            score=float(d.score),
            direction=int(d.direction),
        ))

    last = enriched.iloc[-1]
    prev = enriched.iloc[-2]
    decision = decide(last, prev, scfg,
                      bar_in_session=60, bars_per_session=390)

    # Projected trade plan (always shown, even if direction == 0 the
    # frontend will still draw a +long projection for context using the
    # raw long/short of the score sign).
    rr = float(cfg.get("rr_ratio", 2.0))
    stop_atr_mult = float(cfg.get("stop_atr_mult", 1.5))
    direction = decision.direction or (1 if decision.score >= 0 else -1)
    atr = float(last["atr"])
    plan: TradePlan | None = None
    if atr > 0:
        stop_dist = stop_atr_mult * atr
        entry = float(last["close"])
        if direction > 0:
            stop = entry - stop_dist
            tp = entry + stop_dist * rr
        else:
            stop = entry + stop_dist
            tp = entry - stop_dist * rr
        plan = TradePlan(
            direction=direction,
            entry=entry, stop=stop, take_profit=tp,
            risk_per_share=stop_dist, rr_ratio=rr,
        )

    bars_out: list[IndicatorBar] = []
    for ts, row in enriched.tail(lookback_minutes).iterrows():
        bars_out.append(IndicatorBar(
            ts=str(ts),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
            ema_fast=_safe(row.get("ema_fast")),
            ema_mid=_safe(row.get("ema_mid")),
            ema_slow=_safe(row.get("ema_slow")),
            vwap=_safe(row.get("vwap")),
            bb_up=_safe(row.get("bb_up")),
            bb_lo=_safe(row.get("bb_lo")),
            bb_mid=_safe(row.get("bb_mid")),
            rsi=_safe(row.get("rsi")),
            adx=_safe(row.get("adx")),
            atr=_safe(row.get("atr")),
            macd=_safe(row.get("macd")),
            macd_signal=_safe(row.get("macd_signal")),
            macd_hist=_safe(row.get("macd_hist")),
        ))

    return PredictionResponse(
        symbol=symbol,
        bars=bars_out,
        score_history=history,
        current_score=float(decision.score),
        current_direction=int(decision.direction),
        current_reason=decision.reason,
        current_votes=decision.votes,
        plan=plan,
        last_close=float(last["close"]),
        rsi=float(last["rsi"]),
        adx=float(last["adx"]),
        atr=float(last["atr"]),
        vwap=float(last["vwap"]),
    )
