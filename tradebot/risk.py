"""Risk manager.

Enforces:
  * Fixed-fractional position sizing using ATR-based stop distance
    (stop_atr_mult * ATR per share).  Risk per trade is capped at
    ``risk_per_trade`` of current equity.
  * Take-profit at ``rr_ratio`` * stop distance.
  * Trailing stop that ratchets in the direction of profit.
  * Daily loss circuit breaker (trading halts for the rest of the session).
  * Cooldown after consecutive losses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    starting_equity: float = 100_000.0
    risk_per_trade: float = 0.0075     # 0.75% of equity at risk per trade
    stop_atr_mult: float = 1.5
    rr_ratio: float = 2.2              # take-profit = 2.2x stop distance
    trail_atr_mult: float = 1.2
    max_position_pct: float = 0.35     # don't deploy more than 35% notional
    daily_loss_limit_pct: float = 0.025
    consec_loss_cooldown: int = 3      # bars to pause after this many losers
    cooldown_bars: int = 30


@dataclass
class TradePlan:
    direction: int          # +1 long, -1 short
    entry_price: float
    stop: float
    take_profit: float
    qty: int
    risk_dollars: float


class RiskManager:
    """Stateful risk gatekeeper used by the execution engine."""

    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.equity = cfg.starting_equity
        self.peak_equity = cfg.starting_equity
        self.day_start_equity = cfg.starting_equity
        self.consec_losses = 0
        self.cooldown_remaining = 0
        self.halted_today = False
        self._current_day = None

    # -- session housekeeping ---------------------------------------------

    def on_new_session(self, ts) -> None:
        day = ts.normalize() if hasattr(ts, "normalize") else ts
        if day != self._current_day:
            self._current_day = day
            self.day_start_equity = self.equity
            self.halted_today = False

    # -- gating ------------------------------------------------------------

    def can_open(self) -> tuple[bool, str]:
        if self.halted_today:
            return False, "daily_loss_limit_hit"
        if self.cooldown_remaining > 0:
            return False, "in_cooldown"
        return True, "ok"

    def step_cooldown(self) -> None:
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    # -- planning ----------------------------------------------------------

    def plan(self, direction: int, price: float, atr: float) -> TradePlan | None:
        if atr <= 0 or price <= 0:
            return None
        stop_dist = self.cfg.stop_atr_mult * atr
        if stop_dist <= 0:
            return None

        risk_dollars = self.equity * self.cfg.risk_per_trade
        qty_by_risk = int(risk_dollars // stop_dist)
        max_notional = self.equity * self.cfg.max_position_pct
        qty_by_notional = int(max_notional // price)
        qty = max(0, min(qty_by_risk, qty_by_notional))
        if qty == 0:
            return None

        if direction > 0:
            stop = price - stop_dist
            tp = price + stop_dist * self.cfg.rr_ratio
        else:
            stop = price + stop_dist
            tp = price - stop_dist * self.cfg.rr_ratio

        return TradePlan(
            direction=direction,
            entry_price=price,
            stop=stop,
            take_profit=tp,
            qty=qty,
            risk_dollars=qty * stop_dist,
        )

    # -- trailing stop -----------------------------------------------------

    def trail(self, plan: TradePlan, price: float, atr: float) -> TradePlan:
        trail_dist = self.cfg.trail_atr_mult * atr
        if plan.direction > 0:
            new_stop = max(plan.stop, price - trail_dist)
        else:
            new_stop = min(plan.stop, price + trail_dist)
        return TradePlan(
            direction=plan.direction,
            entry_price=plan.entry_price,
            stop=new_stop,
            take_profit=plan.take_profit,
            qty=plan.qty,
            risk_dollars=plan.risk_dollars,
        )

    # -- post-trade bookkeeping -------------------------------------------

    def record_result(self, pnl: float) -> None:
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= self.cfg.consec_loss_cooldown:
                self.cooldown_remaining = self.cfg.cooldown_bars
                self.consec_losses = 0
        else:
            self.consec_losses = 0

        # Daily loss circuit breaker
        day_pnl_pct = (self.equity - self.day_start_equity) / self.day_start_equity
        if day_pnl_pct <= -self.cfg.daily_loss_limit_pct:
            self.halted_today = True
