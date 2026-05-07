"""Persistent strategy state (singleton row in ``strategy_state`` table)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..models import StrategyState


DEFAULT_CONFIG: dict[str, Any] = {
    "symbols": [settings.default_symbol],
    "entry_threshold": 0.50,
    "adx_min": 22.0,
    "risk_per_trade": settings.risk_per_trade,
    "rr_ratio": settings.rr_ratio,
    "stop_atr_mult": settings.stop_atr_mult,
    "auto_trade": True,
}


def get_or_create(db: Session) -> StrategyState:
    state = db.get(StrategyState, 1)
    if state is None:
        state = StrategyState(
            id=1,
            running=False,
            mode=settings.default_mode,
            kill_switch=False,
            halted_today=False,
            halted_reason=None,
            last_tick=None,
            last_decision=None,
            config=dict(DEFAULT_CONFIG),
        )
        db.add(state)
        db.flush()
    if state.config is None:
        state.config = dict(DEFAULT_CONFIG)
        db.flush()
    return state


def update_decision(db: Session, decision: dict[str, Any]) -> None:
    state = get_or_create(db)
    state.last_decision = decision
    state.last_tick = dt.datetime.now(dt.timezone.utc)
    db.flush()
