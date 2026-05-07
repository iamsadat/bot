"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- account / positions -------------------------------------------

class AccountInfo(BaseModel):
    broker: str
    mode: Literal["paper", "live"]
    cash: float
    equity: float
    buying_power: float
    portfolio_value: float
    day_pnl: float = 0.0
    day_pnl_pct: float = 0.0
    is_market_open: bool = False


class PositionInfo(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    side: Literal["long", "short"]


# ---------- orders ---------------------------------------------------------

class ManualOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    type: Literal["market", "limit", "bracket"] = "market"
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    time_in_force: Literal["day", "gtc"] = "day"
    idempotency_key: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class CancelOrderRequest(BaseModel):
    broker_order_id: Optional[str] = None
    cancel_all: bool = False


class OrderInfo(BaseModel):
    id: Optional[int]
    ts: dt.datetime
    symbol: str
    side: str
    qty: float
    type: str
    status: str
    broker_order_id: Optional[str]
    source: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    rejected_reason: Optional[str] = None


# ---------- strategy / engine ---------------------------------------------

class StrategyConfigSchema(BaseModel):
    symbols: list[str] = ["SPY"]
    entry_threshold: float = 0.50
    adx_min: float = 22.0
    risk_per_trade: float = 0.0075
    rr_ratio: float = 2.0
    stop_atr_mult: float = 1.5
    auto_trade: bool = True


class StrategyStateInfo(BaseModel):
    running: bool
    mode: Literal["paper", "live"]
    kill_switch: bool
    halted_today: bool
    halted_reason: Optional[str]
    last_tick: Optional[dt.datetime]
    last_decision: Optional[dict[str, Any]]
    config: StrategyConfigSchema


# ---------- safety / mode -------------------------------------------------

class ModeChangeRequest(BaseModel):
    mode: Literal["paper", "live"]
    confirmation: Optional[str] = None


# ---------- audit ----------------------------------------------------------

class AuditEntry(BaseModel):
    id: int
    ts: dt.datetime
    kind: str
    actor: str
    summary: str
    detail: Optional[dict[str, Any]] = None


# ---------- market data ----------------------------------------------------

class Bar(BaseModel):
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarsResponse(BaseModel):
    symbol: str
    bars: list[Bar]
