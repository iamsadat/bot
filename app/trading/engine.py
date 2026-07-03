"""Automated trading engine.

A single async loop that runs at ``engine_tick_seconds`` cadence while the
market is open and the user has armed the strategy.  On each tick it:

  1. Classifies the broad market regime from SPY (trend_up / trend_down / chop).
  2. Scans the whole watchlist: pulls minute bars, computes the indicator
     bundle, and gets a confluence decision from ``tradebot.strategy`` for
     every symbol (pass 1).
  3. Filters candidates to those with a non-zero direction that pass
     multi-timeframe confirmation and the news-sentiment filter, ranks the
     survivors, and takes the top ``scanner_top_n`` within available
     portfolio headroom (pass 2).
  4. Sizes and submits an order for each selected candidate — either the
     default bracket order or, if ``managed_exits_enabled``, a managed
     entry with staged breakeven/partial/trailing exits.
  5. Persists the full ranked candidate list + regime to ``StrategyState``
     and broadcasts it on the websocket as a ``scan`` event.

The engine never bypasses the kill-switch, daily-halt, or rate-limit checks.
A single broker handle is owned by the engine; the API routes use a separate
broker handle for read calls.  Every blocking broker call is dispatched via
``asyncio.to_thread`` so a ~25-symbol scan never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import Awaitable, Callable, Optional

import pandas as pd

from tradebot.indicators import compute_all
from tradebot.strategy import StrategyConfig, classify_regime, decide, mtf_confirm

from ..brokers.base import Broker, BrokerError, NotConfiguredError
from ..config import settings
from ..db import session_scope
from ..models import OrderRecord
from . import audit as audit_log
from . import exits as exits_mod
from . import news as news_mod
from . import state as state_mod
from .risk import portfolio_headroom, rate_limit_ok, size_position


log = logging.getLogger("engine")

Broadcast = Callable[[str, dict], Awaitable[None]]

CHOP_THRESHOLD_PENALTY = 0.10


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
            if not await asyncio.to_thread(broker.is_market_open):
                return

            account = await asyncio.to_thread(broker.get_account)
            try:
                positions = await asyncio.to_thread(broker.get_positions)
            except BrokerError as e:
                log.warning("tick: get_positions failed: %s", e)
                positions = []

            watchlist: list[str] = cfg.get("watchlist") or cfg.get("symbols") or [settings.default_symbol]
            base_entry_threshold = float(cfg.get("entry_threshold", 0.5))
            adx_min = float(cfg.get("adx_min", 22.0))
            auto_trade = bool(cfg.get("auto_trade", True))
            scanner_top_n = int(cfg.get("scanner_top_n", settings.scanner_top_n))

            regime, spy_bars = await self._classify_regime(broker)
            effective_entry_threshold = base_entry_threshold
            if regime == "chop":
                effective_entry_threshold += CHOP_THRESHOLD_PENALTY
            scfg = StrategyConfig(entry_threshold=effective_entry_threshold, adx_min=adx_min)

            candidates = await self._scan_watchlist(broker, watchlist, scfg, spy_bars)
            if settings.news_enabled:
                await self._apply_news_filter(broker, candidates)

            selected, headroom = self._select_candidates(
                candidates, account, positions, scanner_top_n,
            )
            candidates.sort(key=lambda c: c["rank_score"], reverse=True)

            scan_payload = {
                "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                "regime": regime,
                "candidates": candidates,
            }
            with session_scope() as db:
                st = state_mod.get_or_create(db)
                st.last_scan = scan_payload
                st.regime = regime
                if candidates:
                    state_mod.update_decision(db, candidates[0])
            await self._broadcast("scan", scan_payload)

            if settings.managed_exits_enabled:
                await exits_mod.manage_open_positions(broker, self._broadcast)

            if not auto_trade or not selected:
                return
            notional_cap = headroom / len(selected)
            for candidate in selected:
                await self._maybe_trade(broker, candidate, account, cfg, notional_cap)

    async def _classify_regime(self, broker) -> tuple[str, pd.DataFrame | None]:
        try:
            spy_bars = await asyncio.to_thread(broker.get_bars, "SPY", lookback_minutes=240)
        except BrokerError as e:
            log.warning("regime: get_bars(SPY) failed: %s", e)
            return "chop", None
        if len(spy_bars) < 80:
            return "chop", spy_bars
        return classify_regime(spy_bars), spy_bars

    async def _scan_watchlist(self, broker, watchlist, scfg, spy_bars) -> list[dict]:
        candidates: list[dict] = []
        for symbol in watchlist:
            if symbol == "SPY" and spy_bars is not None:
                bars = spy_bars
            else:
                try:
                    bars = await asyncio.to_thread(broker.get_bars, symbol, lookback_minutes=240)
                except BrokerError as e:
                    log.warning("scan: get_bars failed for %s: %s", symbol, e)
                    continue
            if len(bars) < 80:
                continue
            enriched = compute_all(bars)
            row = enriched.iloc[-1]
            prev = enriched.iloc[-2]
            bar_in_session = _bars_since_open(enriched.index[-1])
            decision = decide(row, prev, scfg,
                              bar_in_session=bar_in_session,
                              bars_per_session=390)
            mtf_ok = mtf_confirm(decision.direction, bars) if decision.direction != 0 else False

            candidates.append({
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
                "mtf": mtf_ok,
                "news_score": 0.0,
                "news_blocked": False,
                "rank_score": abs(float(decision.score)),
                "selected": False,
            })
        return candidates

    async def _apply_news_filter(self, broker, candidates: list[dict]) -> None:
        for c in candidates:
            if c["direction"] == 0:
                continue
            score = await asyncio.to_thread(self._fetch_news_score, broker, c["symbol"])
            c["news_score"] = score
            c["news_blocked"] = score <= settings.news_negative_block
            if score >= 0.4:
                c["rank_score"] += settings.news_positive_boost

    @staticmethod
    def _fetch_news_score(broker, symbol: str) -> float:
        with session_scope() as db:
            return news_mod.get_cached_score(
                db, broker, symbol, settings.news_lookback_hours,
                settings.news_cache_ttl_seconds,
            )

    @staticmethod
    def _select_candidates(candidates, account, positions, scanner_top_n,
                           ) -> tuple[list[dict], float]:
        eligible = [
            c for c in candidates
            if c["direction"] != 0 and c["mtf"] and not c["news_blocked"]
        ]
        eligible.sort(key=lambda c: c["rank_score"], reverse=True)

        slots, headroom = portfolio_headroom(
            account, positions,
            max_concurrent=settings.max_concurrent_positions,
            max_exposure_pct=settings.max_total_exposure_pct,
        )
        top_n = max(0, min(scanner_top_n, slots, len(eligible)))
        selected = eligible[:top_n]
        selected_symbols = {c["symbol"] for c in selected}
        for c in candidates:
            c["selected"] = c["symbol"] in selected_symbols
        return selected, headroom

    async def _maybe_trade(self, broker, candidate: dict, account, cfg,
                           notional_cap: float | None) -> None:
        symbol = candidate["symbol"]
        if not rate_limit_ok():
            audit_log.write("rate_limited",
                            f"engine rate-limited for {symbol}",
                            actor="engine")
            return
        direction = candidate["direction"]
        price = candidate["price"]
        plan = size_position(
            direction=direction,
            price=price,
            atr=candidate["atr"],
            account=account,
            risk_per_trade=float(cfg.get("risk_per_trade",
                                         settings.risk_per_trade)),
            stop_atr_mult=float(cfg.get("stop_atr_mult",
                                        settings.stop_atr_mult)),
            rr_ratio=float(cfg.get("rr_ratio", settings.rr_ratio)),
            notional_cap=notional_cap,
        )
        if plan is None:
            return

        side = "buy" if direction > 0 else "sell"

        if settings.managed_exits_enabled:
            r_unit = abs(price - plan.stop)
            with session_scope() as db:
                await exits_mod.open_managed(
                    broker, db, symbol=symbol, direction=direction,
                    qty=plan.qty, entry_price=price, r_unit=r_unit,
                )
            await self._broadcast("order", {"symbol": symbol, "side": side,
                                            "qty": plan.qty, "source": "strategy",
                                            "managed": True})
            return

        idem = f"engine-{symbol}-{uuid.uuid4().hex[:12]}"
        try:
            res = await asyncio.to_thread(
                broker.place_order,
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
                            detail={"reason": str(e), "score": candidate["score"]})
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
                extra={"score": candidate["score"], "votes": candidate["votes"]},
            ))
            audit_log.write(
                "order_submitted",
                f"engine: {side} {plan.qty} {symbol} (bracket, "
                f"stop {plan.stop:.2f}, tp {plan.take_profit:.2f})",
                actor="engine",
                detail={"score": candidate["score"], "votes": candidate["votes"],
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
