"""Dependency providers (broker, engine)."""

from __future__ import annotations

from .brokers import AlpacaBroker, Broker
from .config import settings
from .db import session_scope
from .trading import state as state_mod


def make_broker(mode: str | None = None) -> Broker:
    if mode is None:
        with session_scope() as db:
            mode = state_mod.get_or_create(db).mode
    if mode == "live":
        return AlpacaBroker(
            api_key=settings.alpaca_live_key,
            secret_key=settings.alpaca_live_secret,
            mode="live",
        )
    return AlpacaBroker(
        api_key=settings.alpaca_paper_key,
        secret_key=settings.alpaca_paper_secret,
        mode="paper",
    )
