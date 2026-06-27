"""Multi-signal confluence strategy engine.

Decision rule
-------------
For every bar we compute eight independent signals, each producing a vote in
[-1, +1].  The signals are grouped into trend, momentum, mean-reversion and
volume buckets.  A weighted aggregate score is compared against a configurable
threshold; a trade is taken only when:

  * |score| >= entry_threshold,                                        and
  * trend strength (ADX) >= adx_min                                    and
  * price is on the correct side of session VWAP                       and
  * we are inside the allowed intraday trading window
    (avoid the chop of the first ``warmup_bars`` and force exits in the
     last ``cooldown_bars`` of the session).

This confluence approach reduces false positives that any single indicator
would generate.  Every decision carries the breakdown of which signals fired
so that the report and dashboard can explain *why* a trade was taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd


# Vote signs are encoded as +1 bullish, -1 bearish, 0 neutral, scaled by
# strength when the indicator supports it.
SIGNAL_WEIGHTS: Dict[str, float] = {
    # Trend (50 %)
    "ema_stack": 0.18,
    "macd": 0.18,
    "adx_dir": 0.14,
    # Momentum (25 %)
    "rsi": 0.13,
    "stoch": 0.12,
    # Mean reversion (15 %)
    "vwap_dev": 0.08,
    "bb_pos": 0.07,
    # Volume confirmation (10 %)
    "obv": 0.10,
}


@dataclass
class StrategyConfig:
    entry_threshold: float = 0.50
    adx_min: float = 22.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    min_agreeing_signals: int = 5     # of 8 — confluence floor
    warmup_bars: int = 15             # ignore first N bars of each session
    cooldown_bars: int = 10           # close all positions in last N bars
    post_trade_cooldown: int = 3      # bars to wait after exiting a trade
    weights: Dict[str, float] = field(
        default_factory=lambda: dict(SIGNAL_WEIGHTS)
    )


# ---------- per-signal voting functions -----------------------------------

def _ema_stack_vote(row: pd.Series) -> float:
    f, m, s, c = row["ema_fast"], row["ema_mid"], row["ema_slow"], row["close"]
    if c > f > m > s:
        return 1.0
    if c < f < m < s:
        return -1.0
    if c > f > m:
        return 0.5
    if c < f < m:
        return -0.5
    return 0.0


def _macd_vote(row: pd.Series, prev: pd.Series) -> float:
    line, sig, hist = row["macd"], row["macd_signal"], row["macd_hist"]
    prev_hist = prev["macd_hist"]
    base = np.tanh(hist * 50.0)  # squashes the histogram into [-1, 1]
    if line > sig and hist > prev_hist:
        return float(np.clip(0.5 + base / 2, 0.0, 1.0))
    if line < sig and hist < prev_hist:
        return float(np.clip(-0.5 + base / 2, -1.0, 0.0))
    return float(np.clip(base, -0.6, 0.6))


def _adx_dir_vote(row: pd.Series, adx_min: float) -> float:
    if row["adx"] < adx_min:
        return 0.0
    strength = float(np.clip((row["adx"] - adx_min) / 30.0, 0.0, 1.0))
    if row["plus_di"] > row["minus_di"]:
        return strength
    return -strength


def _rsi_vote(row: pd.Series, long_max: float, short_min: float) -> float:
    r = row["rsi"]
    if 50.0 < r < long_max:
        return float((r - 50.0) / (long_max - 50.0))
    if short_min < r < 50.0:
        return -float((50.0 - r) / (50.0 - short_min))
    if r >= long_max:
        return -0.4         # overbought — slight contrarian penalty
    if r <= short_min:
        return 0.4          # oversold — slight contrarian boost
    return 0.0


def _stoch_vote(row: pd.Series, prev: pd.Series) -> float:
    k, d = row["stoch_k"], row["stoch_d"]
    pk, pd_ = prev["stoch_k"], prev["stoch_d"]
    if pk < pd_ and k > d and k < 80:
        return 1.0
    if pk > pd_ and k < d and k > 20:
        return -1.0
    return float(np.clip((k - 50.0) / 50.0, -0.4, 0.4))


def _vwap_vote(row: pd.Series) -> float:
    if row["atr"] <= 0 or np.isnan(row["vwap"]):
        return 0.0
    dev = (row["close"] - row["vwap"]) / row["atr"]
    return float(np.clip(dev / 2.0, -1.0, 1.0))


def _bb_pos_vote(row: pd.Series) -> float:
    pct = row["bb_pct"]
    if np.isnan(pct):
        return 0.0
    # Centred so 0.5 is neutral.  Squeeze breakouts handled by trend signals.
    return float(np.clip((pct - 0.5) * 2.0, -1.0, 1.0))


def _obv_vote(row: pd.Series) -> float:
    if np.isnan(row["obv_ema"]) or row["obv_ema"] == 0:
        return 0.0
    diff = row["obv"] - row["obv_ema"]
    scale = abs(row["obv_ema"]) + 1.0
    return float(np.clip(diff / scale * 5.0, -1.0, 1.0))


# ---------- top-level decision --------------------------------------------

@dataclass
class Decision:
    direction: int                  # +1 long, -1 short, 0 flat
    score: float
    votes: Dict[str, float]
    reason: str


def decide(row: pd.Series, prev: pd.Series, cfg: StrategyConfig,
           bar_in_session: int, bars_per_session: int) -> Decision:
    """Return a trade decision for the current bar.

    ``bar_in_session`` is the 0-indexed bar within the trading day.
    """
    if bar_in_session < cfg.warmup_bars:
        return Decision(0, 0.0, {}, "warmup")
    if bar_in_session >= bars_per_session - cfg.cooldown_bars:
        return Decision(0, 0.0, {}, "cooldown")

    # Hard gate: indicators must be warm.
    if np.isnan(row[["ema_slow", "atr", "macd_signal", "vwap"]]).any():
        return Decision(0, 0.0, {}, "indicator_warmup")

    votes = {
        "ema_stack": _ema_stack_vote(row),
        "macd":      _macd_vote(row, prev),
        "adx_dir":   _adx_dir_vote(row, cfg.adx_min),
        "rsi":       _rsi_vote(row, cfg.rsi_long_max, cfg.rsi_short_min),
        "stoch":     _stoch_vote(row, prev),
        "vwap_dev":  _vwap_vote(row),
        "bb_pos":    _bb_pos_vote(row),
        "obv":       _obv_vote(row),
    }
    score = sum(cfg.weights[k] * v for k, v in votes.items())

    bull_count = sum(1 for v in votes.values() if v >= 0.25)
    bear_count = sum(1 for v in votes.values() if v <= -0.25)

    direction = 0
    reason = "score_below_threshold"
    if score >= cfg.entry_threshold:
        if bull_count < cfg.min_agreeing_signals:
            reason = "insufficient_confluence"
        elif row["adx"] < cfg.adx_min:
            reason = "weak_trend"
        elif row["close"] < row["vwap"]:
            reason = "below_vwap_blocks_long"
        elif row["rsi"] >= cfg.rsi_long_max:
            reason = "overbought_blocks_long"
        else:
            direction = +1
            reason = "long_confluence"
    elif score <= -cfg.entry_threshold:
        if bear_count < cfg.min_agreeing_signals:
            reason = "insufficient_confluence"
        elif row["adx"] < cfg.adx_min:
            reason = "weak_trend"
        elif row["close"] > row["vwap"]:
            reason = "above_vwap_blocks_short"
        elif row["rsi"] <= cfg.rsi_short_min:
            reason = "oversold_blocks_short"
        else:
            direction = -1
            reason = "short_confluence"

    return Decision(direction, float(score), votes, reason)
