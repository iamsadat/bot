"""Dependency providers (broker, engine)."""

from __future__ import annotations

from .brokers import AlpacaBroker, Broker
from .credentials import resolve
from .db import session_scope
from .trading import state as state_mod


def make_broker(mode: str | None = None) -> Broker:
    if mode is None:
        with session_scope() as db:
            mode = state_mod.get_or_create(db).mode
    api_key, api_secret, _ = resolve(mode)  # type: ignore[arg-type]
    return AlpacaBroker(api_key=api_key, secret_key=api_secret, mode=mode)  # type: ignore[arg-type]
