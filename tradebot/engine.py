"""Execution engine — bar-by-bar mock broker + backtest loop.

The engine consumes a DataFrame of bars and an indicator-augmented copy of
the same frame, walks bar by bar, and produces:

  * a list of completed trades (entry/exit/pnl/reason),
  * an equity curve indexed by timestamp,
  * the final ``RiskManager`` (with terminal equity).

Order execution rules (intentionally conservative so backtests are honest):

  * Entries fill at the *next* bar's open (no look-ahead).
  * Stop and take-profit are checked against the current bar's high/low; if
    both could have been hit on the same bar the stop is assumed to fill
    first (worst case for the strategy).
  * A fixed slippage in basis points and a flat per-share commission are
    deducted from every fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from .indicators import compute_all
from .risk import RiskConfig, RiskManager, TradePlan
from .strategy import StrategyConfig, Decision, decide


@dataclass
class ExecConfig:
    slippage_bps: float = 1.0       # 1 bp = 0.01 % per fill
    commission_per_share: float = 0.005


@dataclass
class Trade:
    direction: int
    entry_time: pd.Timestamp
    entry_price: float
    qty: int
    exit_time: pd.Timestamp
    exit_price: float
    pnl: float
    reason: str
    score: float
    bars_held: int


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    bars: pd.DataFrame
    risk: RiskManager
    strategy_cfg: StrategyConfig
    risk_cfg: RiskConfig
    decisions: pd.DataFrame  # per-bar diagnostics
    symbol: str = "MOCK"


def _apply_slippage(price: float, side: int, bps: float) -> float:
    return price * (1.0 + side * bps / 10_000.0)


def run_backtest(
    bars: pd.DataFrame,
    strat_cfg: StrategyConfig | None = None,
    risk_cfg: RiskConfig | None = None,
    exec_cfg: ExecConfig | None = None,
    on_bar: Callable[[dict], None] | None = None,
) -> BacktestResult:
    """Run the backtest.

    ``on_bar`` is an optional hook the dashboard uses to stream live updates.
    """
    strat_cfg = strat_cfg or StrategyConfig()
    risk_cfg = risk_cfg or RiskConfig()
    exec_cfg = exec_cfg or ExecConfig()

    enriched = compute_all(bars)
    rm = RiskManager(risk_cfg)
    open_plan: TradePlan | None = None
    open_entry_time: pd.Timestamp | None = None
    open_entry_bar: int | None = None
    open_score: float = 0.0
    pending_signal: Decision | None = None

    trades: list[Trade] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    decision_rows: list[dict] = []
    post_trade_cooldown: int = 0

    days = enriched.index.normalize()
    bar_in_session = 0
    prev_day = None
    bars_per_session = int((days == days[0]).sum())

    rows = enriched.itertuples(index=True)
    prev_row = None

    for i, raw in enumerate(rows):
        ts = raw.Index
        row = enriched.iloc[i]
        day = days[i]

        # Track session boundaries
        if day != prev_day:
            rm.on_new_session(ts)
            bar_in_session = 0
            prev_day = day
        else:
            bar_in_session += 1

        # ---- 1. Manage open position against current bar's H/L ---------
        if open_plan is not None:
            high, low = row["high"], row["low"]
            exit_price = None
            exit_reason = None

            if open_plan.direction > 0:
                if low <= open_plan.stop:
                    exit_price = min(open_plan.stop, row["open"])
                    exit_reason = "stop"
                elif high >= open_plan.take_profit:
                    exit_price = max(open_plan.take_profit, row["open"])
                    exit_reason = "take_profit"
            else:
                if high >= open_plan.stop:
                    exit_price = max(open_plan.stop, row["open"])
                    exit_reason = "stop"
                elif low <= open_plan.take_profit:
                    exit_price = min(open_plan.take_profit, row["open"])
                    exit_reason = "take_profit"

            # Force flat at end of session
            if (exit_price is None
                    and bar_in_session >= bars_per_session - strat_cfg.cooldown_bars):
                exit_price = row["close"]
                exit_reason = "session_close"

            if exit_price is not None:
                fill = _apply_slippage(exit_price, -open_plan.direction,
                                       exec_cfg.slippage_bps)
                gross = (fill - open_plan.entry_price) * open_plan.qty * open_plan.direction
                commission = exec_cfg.commission_per_share * open_plan.qty * 2
                pnl = gross - commission
                rm.record_result(pnl)
                trades.append(Trade(
                    direction=open_plan.direction,
                    entry_time=open_entry_time,
                    entry_price=open_plan.entry_price,
                    qty=open_plan.qty,
                    exit_time=ts,
                    exit_price=fill,
                    pnl=pnl,
                    reason=exit_reason,
                    score=open_score,
                    bars_held=i - open_entry_bar,
                ))
                open_plan = None
                open_entry_time = None
                open_entry_bar = None
                post_trade_cooldown = strat_cfg.post_trade_cooldown
            else:
                # Trail the stop
                open_plan = rm.trail(open_plan, row["close"], row["atr"])

        # ---- 2. Convert pending signal into a fill at this bar's open --
        if open_plan is None and pending_signal is not None:
            ok, _ = rm.can_open()
            if ok:
                fill_price = _apply_slippage(
                    row["open"], pending_signal.direction, exec_cfg.slippage_bps
                )
                plan = rm.plan(pending_signal.direction, fill_price, row["atr"])
                if plan is not None:
                    open_plan = plan
                    open_entry_time = ts
                    open_entry_bar = i
                    open_score = pending_signal.score
            pending_signal = None

        rm.step_cooldown()

        # ---- 3. Evaluate strategy for next-bar entry -------------------
        if post_trade_cooldown > 0:
            post_trade_cooldown -= 1
            decision = Decision(0, 0.0, {}, "post_trade_cooldown")
        elif prev_row is not None and open_plan is None:
            bars_per_session = max(bars_per_session,
                                   int((days == day).sum()))
            decision = decide(row, prev_row, strat_cfg,
                              bar_in_session, bars_per_session)
            if decision.direction != 0:
                pending_signal = decision
        else:
            decision = Decision(0, 0.0, {}, "no_prev_row")

        # ---- 4. Mark equity, record diagnostics ------------------------
        unrealised = 0.0
        if open_plan is not None:
            unrealised = (row["close"] - open_plan.entry_price) \
                         * open_plan.qty * open_plan.direction
        mark = rm.equity + unrealised
        equity_points.append((ts, mark))

        decision_rows.append({
            "timestamp": ts,
            "score": decision.score,
            "direction": decision.direction,
            "reason": decision.reason,
            "rsi": row["rsi"],
            "adx": row["adx"],
            "atr": row["atr"],
            "vwap": row["vwap"],
            "close": row["close"],
            "equity": mark,
            "in_position": open_plan is not None,
        })

        if on_bar is not None:
            on_bar({
                "i": i,
                "ts": ts,
                "row": row,
                "decision": decision,
                "open_plan": open_plan,
                "equity": mark,
                "trades": trades,
            })

        prev_row = row

    equity_curve = pd.Series(
        [e for _, e in equity_points],
        index=pd.DatetimeIndex([t for t, _ in equity_points], name="timestamp"),
        name="equity",
    )
    decisions = pd.DataFrame(decision_rows).set_index("timestamp")
    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        bars=enriched,
        risk=rm,
        strategy_cfg=strat_cfg,
        risk_cfg=risk_cfg,
        decisions=decisions,
        symbol=bars.attrs.get("symbol", "MOCK"),
    )
