"""SQLAlchemy ORM models.

The only persistent state is:
  * Audit  — append-only log of every interesting event.
  * OrderRecord — every order we attempted, with its broker-side ID.
  * StrategyState — engine flags (running, mode, kill-switch, last tick).
  * AppKv — generic key/value bag for small bits of config.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64))     # "engine", "user", "system"
    summary: Mapped[str] = mapped_column(String(512))
    detail: Mapped[Optional[dict]] = mapped_column(JSON, default=None)


class OrderRecord(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(8))       # paper / live
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))       # buy / sell
    qty: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(16))      # market / limit / bracket
    limit_price: Mapped[Optional[float]] = mapped_column(Float, default=None)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, default=None)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, default=None)
    source: Mapped[str] = mapped_column(String(16))    # manual / strategy
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    rejected_reason: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, default=None)


class StrategyState(Base):
    __tablename__ = "strategy_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(8), default="paper")
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    halted_today: Mapped[bool] = mapped_column(Boolean, default=False)
    halted_reason: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    last_tick: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, default=None)
    last_decision: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )


class AppKv(Base):
    __tablename__ = "app_kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )


class Credential(Base):
    """Runtime-editable broker credentials.

    Keys are stored plaintext (this app is intended for localhost / VPN use
    only).  Environment variables, when present, take precedence over rows
    in this table.
    """
    __tablename__ = "credentials"
    mode: Mapped[str] = mapped_column(String(8), primary_key=True)  # paper/live
    broker: Mapped[str] = mapped_column(String(32), default="alpaca")
    api_key: Mapped[str] = mapped_column(String(128))
    api_secret: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )
