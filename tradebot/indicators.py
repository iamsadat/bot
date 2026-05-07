"""Vectorised technical indicators.

All functions operate on a pandas DataFrame with columns
[open, high, low, close, volume] indexed by timestamp.
Wilder smoothing is used for RSI, ATR and ADX as is standard.
VWAP is computed per session (resets each trading day).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------- helpers --------------------------------------------------------

def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing — equivalent to EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


# ---------- moving averages -----------------------------------------------

def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


# ---------- momentum -------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    rs = _wilder(up, period) / _wilder(down, period).replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    line = fast_ema - slow_ema
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 3):
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k.fillna(50.0), d.fillna(50.0)


# ---------- volatility -----------------------------------------------------

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low),
         (high - prev_close).abs(),
         (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), period)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = sma(close, period)
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, pct_b, bandwidth


# ---------- trend strength -------------------------------------------------

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(high, low, close)
    atr_ = _wilder(tr, period)
    plus_di = 100 * _wilder(pd.Series(plus_dm, index=high.index), period) / atr_
    minus_di = 100 * _wilder(pd.Series(minus_dm, index=high.index), period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx.fillna(0.0), period), plus_di, minus_di


# ---------- volume / VWAP --------------------------------------------------

def vwap_session(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset each session."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    session_id = df.index.normalize()
    cum_pv = pv.groupby(session_id).cumsum()
    cum_v = df["volume"].groupby(session_id).cumsum().replace(0, np.nan)
    return (cum_pv / cum_v).fillna(typical)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


# ---------- one-shot bundle ------------------------------------------------

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the full indicator bundle used by the strategy engine."""
    out = df.copy()
    h, l, c, v = out["high"], out["low"], out["close"], out["volume"]

    out["ema_fast"] = ema(c, 9)
    out["ema_mid"] = ema(c, 21)
    out["ema_slow"] = ema(c, 55)
    out["rsi"] = rsi(c, 14)
    line, sig, hist = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = line, sig, hist
    mid, up, lo, pctb, bw = bollinger(c, 20, 2.0)
    out["bb_mid"], out["bb_up"], out["bb_lo"] = mid, up, lo
    out["bb_pct"], out["bb_bw"] = pctb, bw
    out["atr"] = atr(h, l, c, 14)
    out["vwap"] = vwap_session(out)
    k, d = stochastic(h, l, c, 14, 3)
    out["stoch_k"], out["stoch_d"] = k, d
    adx_, pdi, mdi = adx(h, l, c, 14)
    out["adx"], out["plus_di"], out["minus_di"] = adx_, pdi, mdi
    out["obv"] = obv(c, v)
    out["obv_ema"] = ema(out["obv"], 21)
    return out
