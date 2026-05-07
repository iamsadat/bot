"""Synthetic market data generator.

Produces realistic intraday OHLCV bars with:
  * Geometric Brownian motion baseline
  * Regime switching (trending vs. mean-reverting)
  * U-shaped intraday volatility curve (open/close volatility surge)
  * Rare jump events
  * Volume that correlates with absolute returns
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MarketConfig:
    symbol: str = "MOCK"
    start: str = "2025-01-02 09:30"
    days: int = 20
    minutes_per_day: int = 390      # standard US equity session
    bar_minutes: int = 1
    start_price: float = 100.0
    annual_vol: float = 0.35        # 35% annualised — typical liquid equity
    annual_drift: float = 0.08      # 8% annualised drift
    regime_persistence: float = 0.985
    jump_prob: float = 0.0008       # per-bar
    jump_scale: float = 0.012
    seed: int | None = 42


def _intraday_vol_multiplier(n_bars: int) -> np.ndarray:
    """U-shape: ~1.8x at open/close, ~0.7x at midday."""
    x = np.linspace(-1.0, 1.0, n_bars)
    return 0.7 + 1.1 * (x ** 2)


def generate(cfg: MarketConfig | None = None) -> pd.DataFrame:
    cfg = cfg or MarketConfig()
    rng = np.random.default_rng(cfg.seed)

    bars_per_day = cfg.minutes_per_day // cfg.bar_minutes
    total_bars = bars_per_day * cfg.days
    dt = 1.0 / (252 * bars_per_day)

    sigma_base = cfg.annual_vol * np.sqrt(dt)
    mu = cfg.annual_drift * dt

    intraday_mult = _intraday_vol_multiplier(bars_per_day)
    vol_curve = np.tile(intraday_mult, cfg.days)

    # Two-regime hidden Markov drift modulation: trending vs. mean-reverting.
    regimes = np.zeros(total_bars, dtype=int)
    state = 0
    for i in range(total_bars):
        if rng.random() > cfg.regime_persistence:
            state = 1 - state
        regimes[i] = state
    drift_mult = np.where(regimes == 0, 1.0, -0.4)

    # Jumps
    jumps = rng.random(total_bars) < cfg.jump_prob
    jump_size = rng.normal(0.0, cfg.jump_scale, total_bars) * jumps

    z = rng.standard_normal(total_bars)
    log_ret = mu * drift_mult + sigma_base * vol_curve * z + jump_size

    # Build OHLC by simulating 4 sub-steps per bar so that high/low are realistic.
    sub = 4
    sub_z = rng.standard_normal((total_bars, sub))
    sub_sigma = sigma_base * np.repeat(vol_curve[:, None], sub, axis=1) / np.sqrt(sub)
    sub_log_ret = sub_sigma * sub_z + (log_ret / sub)[:, None]
    sub_paths = np.cumsum(sub_log_ret, axis=1)

    close = np.zeros(total_bars)
    high = np.zeros(total_bars)
    low = np.zeros(total_bars)
    open_ = np.zeros(total_bars)

    price = cfg.start_price
    for i in range(total_bars):
        log_path = sub_paths[i]
        bar_prices = price * np.exp(log_path)
        open_[i] = price
        close[i] = bar_prices[-1]
        high[i] = max(price, bar_prices.max())
        low[i] = min(price, bar_prices.min())
        price = close[i]

    # Volume: base + reaction to absolute log-return + open/close surge.
    abs_ret = np.abs(log_ret)
    base_vol = 5_000
    react = (abs_ret / sigma_base) * 4_000
    surge = (vol_curve - 0.7) * 6_000
    noise = rng.normal(0.0, 800, total_bars)
    volume = np.maximum(0, base_vol + react + surge + noise).astype(int)

    # Timestamp index — only trading minutes per day.
    start_ts = pd.Timestamp(cfg.start)
    timestamps = []
    for d in range(cfg.days):
        day_start = start_ts.normalize() + pd.Timedelta(days=d)
        # Skip weekends
        while day_start.weekday() >= 5:
            day_start += pd.Timedelta(days=1)
        session_start = day_start + pd.Timedelta(
            hours=start_ts.hour, minutes=start_ts.minute
        )
        timestamps.extend(
            session_start + pd.Timedelta(minutes=cfg.bar_minutes * j)
            for j in range(bars_per_day)
        )

    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )
    df.attrs["symbol"] = cfg.symbol
    return df


def session_boundaries(df: pd.DataFrame) -> np.ndarray:
    """Boolean mask: True at first bar of each trading day."""
    return df.index.normalize().to_series().diff().ne(pd.Timedelta(0)).to_numpy()
