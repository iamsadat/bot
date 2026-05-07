"""Alpaca broker adapter.

Wraps ``alpaca-py``'s TradingClient and StockHistoricalDataClient.  All SDK
imports are lazy so that the FastAPI app boots even without ``alpaca-py``
installed; you only need it before you actually try to call the broker.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import pandas as pd

from .base import (
    AccountSnapshot,
    Broker,
    BrokerError,
    NotConfiguredError,
    OrderResult,
    PositionSnapshot,
)

log = logging.getLogger("alpaca")


class AlpacaBroker(Broker):
    """Single-account Alpaca adapter.  Choose paper or live at construction."""

    name = "alpaca"

    def __init__(self, api_key: str | None, secret_key: str | None,
                 mode: Literal["paper", "live"]):
        self._api_key = api_key
        self._secret = secret_key
        self.mode = mode
        self._trading = None
        self._data = None

    # -- lazy clients ------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._api_key and self._secret)

    def _require(self):
        if not self.is_configured():
            raise NotConfiguredError(
                f"Alpaca {self.mode} keys not set. "
                f"Set ALPACA_{self.mode.upper()}_KEY and "
                f"ALPACA_{self.mode.upper()}_SECRET."
            )

    def _trading_client(self):
        if self._trading is None:
            self._require()
            from alpaca.trading.client import TradingClient
            self._trading = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret,
                paper=(self.mode == "paper"),
            )
        return self._trading

    def _data_client(self):
        if self._data is None:
            self._require()
            from alpaca.data.historical import StockHistoricalDataClient
            self._data = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret,
            )
        return self._data

    # -- account / positions ----------------------------------------------

    def get_account(self) -> AccountSnapshot:
        try:
            a = self._trading_client().get_account()
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.get_account failed: {e}") from e
        equity = float(a.equity)
        last_eq = float(a.last_equity) if a.last_equity else equity
        day_pnl = equity - last_eq
        day_pnl_pct = (day_pnl / last_eq) if last_eq else 0.0
        return AccountSnapshot(
            cash=float(a.cash),
            equity=equity,
            buying_power=float(a.buying_power),
            portfolio_value=float(a.portfolio_value),
            day_pnl=day_pnl,
            day_pnl_pct=day_pnl_pct,
        )

    def get_positions(self) -> list[PositionSnapshot]:
        try:
            ps = self._trading_client().get_all_positions()
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.get_positions failed: {e}") from e
        out: list[PositionSnapshot] = []
        for p in ps:
            qty = float(p.qty)
            out.append(PositionSnapshot(
                symbol=p.symbol,
                qty=qty,
                avg_entry_price=float(p.avg_entry_price),
                market_price=float(getattr(p, "current_price", p.avg_entry_price)),
                market_value=float(p.market_value),
                unrealized_pnl=float(p.unrealized_pl),
                unrealized_pnl_pct=float(p.unrealized_plpc),
                side="long" if qty >= 0 else "short",
            ))
        return out

    def get_open_orders(self) -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        try:
            orders = self._trading_client().get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
            )
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.get_open_orders failed: {e}") from e
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty) if o.qty else None,
                "type": o.type.value,
                "status": o.status.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
            }
            for o in orders
        ]

    # -- order placement --------------------------------------------------

    def place_order(self, *, symbol, side, qty, type="market",
                    limit_price=None, stop_loss=None, take_profit=None,
                    time_in_force="day", client_order_id=None) -> OrderResult:
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        kw = dict(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            client_order_id=client_order_id,
        )
        if type == "bracket":
            if take_profit is None or stop_loss is None:
                raise BrokerError(
                    "Bracket order requires both take_profit and stop_loss."
                )
            kw["order_class"] = OrderClass.BRACKET
            kw["take_profit"] = TakeProfitRequest(limit_price=round(take_profit, 2))
            kw["stop_loss"] = StopLossRequest(stop_price=round(stop_loss, 2))
            req = MarketOrderRequest(**kw)
        elif type == "limit":
            if limit_price is None:
                raise BrokerError("Limit order requires limit_price.")
            req = LimitOrderRequest(limit_price=round(limit_price, 2), **kw)
        else:
            req = MarketOrderRequest(**kw)

        try:
            o = self._trading_client().submit_order(req)
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.submit_order failed: {e}") from e

        return OrderResult(
            broker_order_id=str(o.id),
            status=o.status.value if hasattr(o.status, "value") else str(o.status),
            raw={"client_order_id": getattr(o, "client_order_id", None)},
        )

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._trading_client().cancel_order_by_id(broker_order_id)
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.cancel_order failed: {e}") from e

    def cancel_all(self) -> int:
        try:
            res = self._trading_client().cancel_orders()
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.cancel_all failed: {e}") from e
        return len(res or [])

    def close_position(self, symbol: str) -> None:
        try:
            self._trading_client().close_position(symbol)
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.close_position failed: {e}") from e

    def close_all_positions(self) -> int:
        try:
            res = self._trading_client().close_all_positions(cancel_orders=True)
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.close_all_positions failed: {e}") from e
        return len(res or [])

    # -- market data ------------------------------------------------------

    def get_bars(self, symbol: str, lookback_minutes: int = 240) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(minutes=lookback_minutes + 30)
        try:
            resp = self._data_client().get_stock_bars(StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
            ))
        except NotConfiguredError:
            raise
        except Exception as e:                                  # noqa: BLE001
            raise BrokerError(f"alpaca.get_bars failed: {e}") from e
        rows = []
        bars = resp.data.get(symbol, []) if hasattr(resp, "data") else []
        for b in bars:
            rows.append({
                "timestamp": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            })
        if not rows:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
                index=pd.DatetimeIndex([], name="timestamp"),
            )
        df = pd.DataFrame(rows).set_index("timestamp").sort_index()
        df.attrs["symbol"] = symbol
        return df.tail(lookback_minutes)

    def is_market_open(self) -> bool:
        try:
            return bool(self._trading_client().get_clock().is_open)
        except NotConfiguredError:
            return False
        except Exception as e:                                  # noqa: BLE001
            log.warning("alpaca.get_clock failed: %s", e)
            return False
