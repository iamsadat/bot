"""Nightly parameter auto-tuner.

Runs a grid search over recent minute bars using a small standalone replay
simulator (NOT ``tradebot.engine`` — that engine is coupled to its own
synthetic market generator, not a bar-driven replay).  Deliberately coarse:
it scores R-multiples only, so no account/equity simulation is needed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from tradebot.indicators import compute_all
from tradebot.strategy import StrategyConfig, decide

from ..config import settings
from ..db import session_scope
from ..models import TunerProposal
from . import audit as audit_log
from . import state as state_mod
from .engine import _bars_since_open

log = logging.getLogger("tuner")

_GRID_ENTRY_THRESHOLD = (0.4, 0.5, 0.6)
_GRID_ADX_MIN = (18.0, 22.0, 26.0)
_GRID_STOP_ATR_MULT = (1.2, 1.5, 2.0)
_GRID_RR_RATIO = (1.5, 2.0, 2.5)


async def tuner_loop(stop_event: asyncio.Event) -> None:
    tz = ZoneInfo("America/New_York")
    while not stop_event.is_set():
        now = dt.datetime.now(tz)
        target = now.replace(hour=16, minute=15, second=0, microsecond=0)
        if now >= target:
            target += dt.timedelta(days=1)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=(target - now).total_seconds())
            break
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            await asyncio.to_thread(run_tuning_pass)
        except Exception:                                       # noqa: BLE001
            log.exception("tuner pass failed")


def _simulate(bars_by_symbol: dict, entry_threshold: float, adx_min: float,
             stop_atr_mult: float, rr_ratio: float) -> dict:
    """Bar-close-decision / next-open-entry replay. Scores R-multiples only."""
    cfg = StrategyConfig(entry_threshold=entry_threshold, adx_min=adx_min)
    r_multiples: list[float] = []
    for bars in bars_by_symbol.values():
        enriched = compute_all(bars)
        if len(enriched) < 100:
            continue
        position = None
        for i in range(60, len(enriched) - 1):
            bar_in_session = _bars_since_open(enriched.index[i])
            if position is None:
                row, prev = enriched.iloc[i], enriched.iloc[i - 1]
                decision = decide(row, prev, cfg, bar_in_session=bar_in_session,
                                  bars_per_session=390)
                if decision.direction == 0 or row["atr"] <= 0:
                    continue
                stop_dist = stop_atr_mult * row["atr"]
                entry_price = float(enriched.iloc[i + 1]["open"])
                d = decision.direction
                position = {
                    "direction": d, "entry": entry_price, "stop_dist": stop_dist,
                    "stop": entry_price - stop_dist * d,
                    "tp": entry_price + stop_dist * rr_ratio * d,
                }
                continue
            bar = enriched.iloc[i]
            d = position["direction"]
            hit_stop = bar["low"] <= position["stop"] if d > 0 else bar["high"] >= position["stop"]
            hit_tp = bar["high"] >= position["tp"] if d > 0 else bar["low"] <= position["tp"]
            eod = bar_in_session >= 384
            if hit_stop or hit_tp or eod:
                exit_price = position["stop"] if hit_stop else position["tp"] if hit_tp else float(bar["close"])
                r_multiples.append((exit_price - position["entry"]) / position["stop_dist"] * d)
                position = None

    trades = len(r_multiples)
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]
    expectancy = sum(r_multiples) / trades if trades else 0.0
    loss_sum = abs(sum(losses))
    profit_factor = (sum(wins) / loss_sum) if loss_sum else (999.0 if wins else 0.0)
    return {"trades": trades, "expectancy": expectancy, "profit_factor": profit_factor}


def run_tuning_pass() -> None:
    """Sync grid-search pass. Call via ``asyncio.to_thread`` from async code."""
    from ..deps import make_broker

    broker = make_broker()
    symbols = list(settings.watchlist[:10])
    lookback_minutes = settings.tuner_lookback_days * 390 + 60
    bars_by_symbol = {}
    for sym in symbols:
        try:
            bars = broker.get_bars(sym, lookback_minutes=lookback_minutes)
        except Exception:                                        # noqa: BLE001
            continue
        if len(bars) >= 200:
            bars_by_symbol[sym] = bars
    if not bars_by_symbol:
        audit_log.write("tuner_run", "tuner: no bars available, skipped", actor="engine")
        return

    best = None
    for entry_threshold in _GRID_ENTRY_THRESHOLD:
        for adx_min in _GRID_ADX_MIN:
            for stop_atr_mult in _GRID_STOP_ATR_MULT:
                for rr_ratio in _GRID_RR_RATIO:
                    metrics = _simulate(bars_by_symbol, entry_threshold, adx_min,
                                        stop_atr_mult, rr_ratio)
                    if metrics["trades"] < 10:
                        continue
                    score = metrics["expectancy"] * metrics["trades"]
                    if best is None or score > best["score"] or (
                        score == best["score"]
                        and metrics["profit_factor"] > best["metrics"]["profit_factor"]
                    ):
                        best = {
                            "score": score,
                            "params": {"entry_threshold": entry_threshold, "adx_min": adx_min,
                                      "stop_atr_mult": stop_atr_mult, "rr_ratio": rr_ratio},
                            "metrics": metrics,
                        }
    if best is None:
        audit_log.write("tuner_run", "tuner: no combo produced >=10 trades", actor="engine")
        return

    with session_scope() as db:
        proposal = TunerProposal(params=best["params"], metrics=best["metrics"], applied=False)
        db.add(proposal)
        db.flush()
        audit_log.write("tuner_run", f"tuner proposal #{proposal.id}: {best['params']}",
                        actor="engine", db=db, detail=best)
        if settings.tuner_auto_apply:
            st = state_mod.get_or_create(db)
            st.config = {**(st.config or {}), **best["params"]}
            proposal.applied = True
            proposal.applied_at = dt.datetime.now(dt.timezone.utc)
            audit_log.write("tuner_applied", f"tuner auto-applied {best['params']}",
                            actor="engine", db=db)
