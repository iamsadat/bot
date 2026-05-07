"""Broker abstraction.

Concrete brokers (currently only Alpaca) implement this interface.  The
trading engine and the API routes both depend only on ``Broker`` so we can
swap in a different venue later without touching trading logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import pandas as pd


class BrokerError(RuntimeError):
    """Recoverable broker-side error (network, broker rejection, etc.)."""


class NotConfiguredError(BrokerError):
    """Raised when the broker has no API credentials configured."""


@dataclass
class AccountSnapshot:
    cash: float
    equity: float
    buying_power: float
    portfolio_value: float
    day_pnl: float
    day_pnl_pct: float


@dataclass
class PositionSnapshot:
    symbol: str
    qty: float
    avg_entry_price: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    side: Literal["long", "short"]


@dataclass
class OrderResult:
    broker_order_id: str
    status: str
    raw: dict


class Broker(ABC):
    name: str = "base"
    mode: Literal["paper", "live"] = "paper"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    def get_positions(self) -> list[PositionSnapshot]: ...

    @abstractmethod
    def get_open_orders(self) -> list[dict]: ...

    @abstractmethod
    def place_order(
        self,
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        type: Literal["market", "limit", "bracket"] = "market",
        limit_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        time_in_force: Literal["day", "gtc"] = "day",
        client_order_id: str | None = None,
    ) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def cancel_all(self) -> int: ...

    @abstractmethod
    def close_position(self, symbol: str) -> None: ...

    @abstractmethod
    def close_all_positions(self) -> int: ...

    @abstractmethod
    def get_bars(self, symbol: str, lookback_minutes: int = 240) -> pd.DataFrame: ...

    @abstractmethod
    def is_market_open(self) -> bool: ...
