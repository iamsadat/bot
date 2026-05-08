"""Live market data stream.

Wraps Alpaca's ``StockDataStream`` (WebSocket) and re-broadcasts trades,
quotes, and minute bars through our app-level WebSocket hub so that the
frontend chart updates in real time.

Subscriptions are dynamic: the frontend (or the strategy engine) calls
``ensure_subscribed`` for the symbols it cares about.  When the subscription
set changes, the underlying stream is restarted — Alpaca's SDK doesn't
expose a clean live-resubscribe.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .credentials import resolve

log = logging.getLogger("market_stream")

Broadcast = Callable[[str, dict], Awaitable[None]]


class MarketStream:
    def __init__(self, broadcast: Broadcast):
        self._broadcast = broadcast
        self._subs: set[str] = set()
        self._task: asyncio.Task | None = None
        self._stream = None
        self._lock = asyncio.Lock()
        self._mode = "paper"

    @property
    def subscriptions(self) -> list[str]:
        return sorted(self._subs)

    # -- subscription management ------------------------------------------

    async def ensure_subscribed(self, symbols: list[str], mode: str = "paper") -> None:
        symbols = [s.upper() for s in symbols if s]
        async with self._lock:
            new = set(symbols)
            if new == self._subs and self._task and not self._task.done():
                return
            self._subs = new
            self._mode = mode
            await self._restart_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    # -- internal ---------------------------------------------------------

    async def _stop_locked(self) -> None:
        if self._stream is not None:
            try:
                await self._stream.stop_ws()
            except Exception:                                    # noqa: BLE001
                pass
            self._stream = None
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def _restart_locked(self) -> None:
        await self._stop_locked()
        if not self._subs:
            return

        api_key, api_secret, _ = resolve(self._mode)  # type: ignore[arg-type]
        if not api_key or not api_secret:
            log.warning("market_stream: no %s credentials, not starting", self._mode)
            return

        try:
            from alpaca.data.live import StockDataStream
        except ImportError:
            log.error("alpaca-py not installed; market stream disabled")
            return

        self._stream = StockDataStream(api_key=api_key, secret_key=api_secret)
        symbols = list(self._subs)

        async def _on_bar(bar):
            await self._broadcast("bar", {
                "symbol": bar.symbol,
                "ts": bar.timestamp.isoformat() if hasattr(bar.timestamp, "isoformat") else str(bar.timestamp),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            })

        async def _on_trade(trade):
            await self._broadcast("trade", {
                "symbol": trade.symbol,
                "ts": trade.timestamp.isoformat() if hasattr(trade.timestamp, "isoformat") else str(trade.timestamp),
                "price": float(trade.price),
                "size": float(trade.size),
            })

        async def _on_quote(quote):
            await self._broadcast("quote", {
                "symbol": quote.symbol,
                "ts": quote.timestamp.isoformat() if hasattr(quote.timestamp, "isoformat") else str(quote.timestamp),
                "bid": float(quote.bid_price) if quote.bid_price else None,
                "ask": float(quote.ask_price) if quote.ask_price else None,
                "bid_size": float(quote.bid_size) if quote.bid_size else None,
                "ask_size": float(quote.ask_size) if quote.ask_size else None,
            })

        self._stream.subscribe_bars(_on_bar, *symbols)
        self._stream.subscribe_trades(_on_trade, *symbols)
        self._stream.subscribe_quotes(_on_quote, *symbols)

        async def _runner():
            try:
                await self._stream._run_forever()
            except Exception as e:                              # noqa: BLE001
                log.error("market_stream runner failed: %s", e)
            finally:
                log.info("market_stream runner exited")

        self._task = asyncio.create_task(_runner(), name="market-stream")
        log.info("market_stream subscribed to %s in %s mode",
                 ", ".join(symbols), self._mode)
        await self._broadcast("stream_status", {"subscribed": symbols,
                                                "mode": self._mode})
