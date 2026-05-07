"""Automated trading engine.

A single async loop that runs at ``engine_tick_seconds`` cadence while the
market is open and the user has armed the strategy.  On each tick it:

  1. Pulls recent minute bars from the broker.
  2. Computes the indicator bundle and a confluence decision using the
     ``tradebot.strategy`` engine (re-used from the mock bot).
  3. If the decision is non-zero, sizes a bracket order via ``risk.size_position``
     and submits it through the broker.
  4. Persists the decision + audit entry, broadcasts it on the websocket.

The engine never bypasses the kill-switch, daily-halt, or rate-limit checks.
A single broker handle is owned by the engine; the API routes use a separate
broker handle for read calls.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import Awaitable, Callable, Optional

import pandas as pd

from tradebot.indicators import compute_all
from tradebot.strategy import StrategyConfig, decide

from ..brokers.base import Broker, BrokerError, NotConfiguredError
from ..config import settings
from ..db import session_scope
from ..models import OrderRecord
from . import audit as audit_log
from . import state as state_mod
from .risk import assess_order, rate_limit_ok, size_position


log = logging.getLogger("engine")

Broadcast = Callable[[str, dict], Awaitable[None]]


class TradingEngine:
    """Async, single-process scheduler that drives one broker handle."""

    def __init__(self, broker_factory: Callable[[str], Broker],
                 broadcast: Optional[Broadcast] = None):
        self._broker_factory = broker_factory
        self._broadcast = broadcast or (lambda *a, **kw: _noop())
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self._broker: Broker | None = None
        self._mode: str = settings.default_mode

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        with session_scope() as db:
            st = state_mod.get_or_create(db)
            st.running = True
            audit_log.write("engine_start",
                            f"engine started in {st.mode} mode",
                            actor="user", db=db)
            self._mode = st.mode
        self._broker = self._broker_factory(self._mode)
        self._task = asyncio.create_task(self._loop(), name="trading-engine")

    async def stop(self) -> None:
        was_running = bool(self._task and not self._task.done())
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._task = None
        if not was_running:
            return
        with session_scope() as db:
            st = state_mod.get_or_create(db)
            st.running = False
            audit_log.write("engine_stop", "engine stopped",
                            actor="user", db=db)

    async def kill(self) -> None:
        """Hard kill: cancel all open orders, flatten all positions, halt."""
        await self.stop()
        broker = self._broker_factory(self._mode)
        cancelled, flattened = 0, 0
        try:
            cancelled = broker.cancel_all()
        except BrokerError as e:
            log.error("kill: cancel_all failed: %s", e)
        try:
            flattened = broker.close_all_positions()
        except BrokerError as e:
            log.error("kill: close_all_positions failed: %s", e)
        with session_scope() as db:
            st = state_mod.get_or_create(db)
            st.kill_switch = True
            st.halted_today = True
            st.halted_reason = "kill_switch"
            audit_log.write(
                "kill_switch",
                f"kill switch engaged — cancelled {cancelled} orders, "
                f"flattened {flattened} positions",
                actor="user", db=db,
            )
        await self._broadcast("kill", {"cancelled": cancelled,
                                       "flattened": flattened})

    async def reset_kill_switch(self) -> None:
        with session_scope() as db:
            st = state_mod.get_or_create(db)
            st.kill_switch = False
            st.halted_today = False
            st.halted_reason = None
            audit_log.write("kill_reset", "kill switch released",
                            actor="user", db=db)

    # -- main loop --------------------------------------------------------

    async def _loop(self) -> None:
        log.info("engine loop started in %s mode", self._mode)
        try:
            while not self._stop.is_set():
                try:
                    await self._tick()
                except Exception as e:                          # noqa: BLE001
                    log.exception("engine tick failed: %s", e)
                    audit_log.write("engine_error", f"tick failed: {e}",
                                    actor="engine")
                try:
                    await asyncio.wait_for(self._stop.wait(),
                                           timeout=settings.engine_tick_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("engine loop exited")

    async def _tick(self) -> None:
        async with self._tick_lock:
            with session_scope() as db:
                st = state_mod.get_or_create(db)
                if st.kill_switch or st.halted_today:
                    return
                cfg = dict(st.config or {})

            broker = self._broker
            if broker is None or not broker.is_configured():
                return
            if not broker.is_market_open():
                return

            account = broker.get_account()
            symbols: list[str] = cfg.get("symbols") or [settings.default_symbol]
            scfg = StrategyConfig(
                entry_threshold=float(cfg.get("entry_threshold", 0.5)),
                adx_min=float(cfg.get("adx_min", 22.0)),
            )
            auto_trade = bool(cfg.get("auto_trade", True))

            for symbol in symbols:
                bars = broker.get_bars(symbol, lookback_minutes=240)
                if len(bars) < 80:
                    continue
                enriched = compute_all(bars)
                row = enriched.iloc[-1]
                prev = enriched.iloc[-2]
                bar_in_session = _bars_since_open(enriched.index[-1])
                decision = decide(row, prev, scfg,
                                  bar_in_session=bar_in_session,
                                  bars_per_session=390)

                payload = {
                    "symbol": symbol,
                    "ts": str(enriched.index[-1]),
                    "score": float(decision.score),
                    "direction": int(decision.direction),
                    "reason": decision.reason,
                    "votes": decision.votes,
                    "price": float(row["close"]),
                    "rsi": float(row["rsi"]),
                    "adx": float(row["adx"]),
                    "vwap": float(row["vwap"]),
                    "atr": float(row["atr"]),
                }
                with session_scope() as db:
                    state_mod.update_decision(db, payload)
                await self._broadcast("decision", payload)

                if not auto_trade or decision.direction == 0:
                    continue
                await self._maybe_trade(broker, symbol, decision, row, account, cfg)

    async def _maybe_trade(self, broker, symbol, decision, row,
                           account, cfg) -> None:
        if not rate_limit_ok():
            audit_log.write("rate_limited",
                            f"engine rate-limited for {symbol}",
                            actor="engine")
            return
        plan = size_position(
            direction=decision.direction,
            price=float(row["close"]),
            atr=float(row["atr"]),
            account=account,
            risk_per_trade=float(cfg.get("risk_per_trade",
                                         settings.risk_per_trade)),
            stop_atr_mult=float(cfg.get("stop_atr_mult",
                                        settings.stop_atr_mult)),
            rr_ratio=float(cfg.get("rr_ratio", settings.rr_ratio)),
        )
        if plan is None:
            return

        idem = f"engine-{symbol}-{uuid.uuid4().hex[:12]}"
        side = "buy" if decision.direction > 0 else "sell"
        try:
            res = broker.place_order(
                symbol=symbol,
                side=side,
                qty=plan.qty,
                type="bracket",
                stop_loss=plan.stop,
                take_profit=plan.take_profit,
                client_order_id=idem,
            )
        except BrokerError as e:
            audit_log.write("order_rejected",
                            f"{side} {plan.qty} {symbol} rejected: {e}",
                            actor="engine",
                            detail={"reason": str(e), "score": decision.score})
            return

        with session_scope() as db:
            db.add(OrderRecord(
                idempotency_key=idem,
                broker=broker.name,
                mode=broker.mode,
                symbol=symbol,
                side=side,
                qty=plan.qty,
                type="bracket",
                stop_price=plan.stop,
                take_profit=plan.take_profit,
                source="strategy",
                status=res.status,
                broker_order_id=res.broker_order_id,
                extra={"score": decision.score, "votes": decision.votes},
            ))
            audit_log.write(
                "order_submitted",
                f"engine: {side} {plan.qty} {symbol} (bracket, "
                f"stop {plan.stop:.2f}, tp {plan.take_profit:.2f})",
                actor="engine",
                detail={"score": decision.score, "votes": decision.votes,
                        "broker_order_id": res.broker_order_id},
                db=db,
            )
        await self._broadcast("order", {"symbol": symbol, "side": side,
                                        "qty": plan.qty,
                                        "broker_order_id": res.broker_order_id,
                                        "source": "strategy"})


async def _noop():
    return None


def _bars_since_open(ts: pd.Timestamp) -> int:
    """Approximate the bar index inside the US-equities cash session.

    The strategy uses this to enforce its warmup / cooldown windows.  We
    treat 13:30 UTC as the open (≈ 09:30 ET) and clamp to the 0–389 window.
    """
    t = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    open_t = t.normalize() + pd.Timedelta(hours=13, minutes=30)
    delta = (t - open_t).total_seconds() / 60.0
    return max(0, min(389, int(delta)))
