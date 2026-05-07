"""Pre-trade risk gate.

Server-side enforcement of:
  * Kill switch / halted-today (no new orders).
  * Daily loss circuit breaker.
  * Per-trade dollar risk (sized from account equity + ATR stop).
  * Max position notional.
  * Order-rate limit (sliding 60-second window).

Every check returns either ``("ok", info)`` or ``("reject", reason)`` so the
caller can emit a clean rejection with an audit entry.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque

from ..brokers.base import AccountSnapshot
from ..config import settings


@dataclass
class SizedOrder:
    qty: int
    stop: float
    take_profit: float
    risk_dollars: float


class RateLimiter:
    """Simple sliding-window rate limiter (process-local)."""

    def __init__(self, max_per_minute: int):
        self.max = max_per_minute
        self.q: Deque[float] = deque()
        self.lock = Lock()

    def allow(self) -> bool:
        now = dt.datetime.now().timestamp()
        cutoff = now - 60.0
        with self.lock:
            while self.q and self.q[0] < cutoff:
                self.q.popleft()
            if len(self.q) >= self.max:
                return False
            self.q.append(now)
            return True


_rate_limiter = RateLimiter(settings.max_orders_per_minute)


def rate_limit_ok() -> bool:
    return _rate_limiter.allow()


def size_position(*, direction: int, price: float, atr: float,
                  account: AccountSnapshot,
                  risk_per_trade: float = settings.risk_per_trade,
                  stop_atr_mult: float = settings.stop_atr_mult,
                  rr_ratio: float = settings.rr_ratio,
                  max_notional_pct: float = settings.max_position_notional_pct,
                  ) -> SizedOrder | None:
    """Return a fully-validated, integer-share order plan, or None if invalid."""
    if direction not in (-1, 1):
        return None
    if atr <= 0 or price <= 0:
        return None
    stop_dist = stop_atr_mult * atr
    if stop_dist <= 0:
        return None

    risk_dollars = account.equity * risk_per_trade
    qty_by_risk = math.floor(risk_dollars / stop_dist)
    qty_by_notional = math.floor(account.equity * max_notional_pct / price)
    qty_by_buying_power = math.floor(account.buying_power / price)
    qty = max(0, min(qty_by_risk, qty_by_notional, qty_by_buying_power))
    if qty == 0:
        return None

    if direction > 0:
        stop = price - stop_dist
        tp = price + stop_dist * rr_ratio
    else:
        stop = price + stop_dist
        tp = price - stop_dist * rr_ratio
    return SizedOrder(qty=int(qty), stop=stop, take_profit=tp,
                      risk_dollars=qty * stop_dist)


def assess_order(
    *,
    kill_switch: bool,
    halted_today: bool,
    qty: float,
    price: float,
    account: AccountSnapshot,
) -> tuple[str, str]:
    """Check coarse rails for a manual order.  Returns (verdict, reason)."""
    if kill_switch:
        return "reject", "kill_switch_active"
    if halted_today:
        return "reject", "daily_halt_active"
    if not rate_limit_ok():
        return "reject", "rate_limit_exceeded"
    notional = qty * price
    if notional <= 0:
        return "reject", "invalid_notional"
    if notional > account.buying_power:
        return "reject", "insufficient_buying_power"
    if notional > account.equity * settings.max_position_notional_pct:
        return "reject", "notional_exceeds_max_position_pct"
    if account.day_pnl_pct <= -settings.daily_loss_limit_pct:
        return "reject", "daily_loss_limit_hit"
    return "ok", "ok"
