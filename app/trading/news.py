"""Lightweight headline sentiment filter.

Pure, hand-built lexicon scoring — no external NLP dependency for a signal
this coarse.  Score is cached per-symbol in ``AppKv`` to avoid hammering the
news endpoint every tick.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..brokers.base import Broker
from ..models import AppKv

log = logging.getLogger("news")

POSITIVE_WORDS = {
    "beat", "beats", "upgrade", "upgraded", "surge", "surges", "record",
    "approval", "approved", "buyback", "rally", "rallies", "outperform",
    "raise", "raised", "raises", "strong", "growth", "profit", "profitable",
    "expansion", "partnership", "breakthrough", "bullish", "win", "wins",
    "soar", "soars", "top", "tops", "exceed", "exceeds",
}
NEGATIVE_WORDS = {
    "miss", "misses", "downgrade", "downgraded", "lawsuit", "probe",
    "recall", "bankruptcy", "fraud", "plunge", "plunges", "layoffs",
    "layoff", "cut", "cuts", "warning", "investigation", "sec", "fine",
    "fined", "delist", "delisted", "bearish", "slump", "slumps", "loss",
    "losses", "weak", "decline", "declines", "halt", "halted",
}


def _headline_valence(text: str) -> float:
    words = text.lower().split()
    pos = sum(1 for w in words if w.strip(".,!?:;\"'") in POSITIVE_WORDS)
    neg = sum(1 for w in words if w.strip(".,!?:;\"'") in NEGATIVE_WORDS)
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def score_headlines(items: list[dict]) -> float:
    """Average per-headline valence in [-1, 1], weighted toward recent items."""
    if not items:
        return 0.0
    scored = []
    for i, item in enumerate(items):
        text = f"{item.get('headline', '')} {item.get('summary', '')}"
        valence = _headline_valence(text)
        weight = 1.0 / (1.0 + i * 0.15)  # items are newest-first from the API
        scored.append((valence, weight))
    total_weight = sum(w for _, w in scored)
    if total_weight == 0:
        return 0.0
    return max(-1.0, min(1.0, sum(v * w for v, w in scored) / total_weight))


def get_cached_score(db: Session, broker: Broker, symbol: str,
                     lookback_hours: int, ttl_seconds: int) -> float:
    key = f"news:{symbol}"
    row = db.get(AppKv, key)
    now = dt.datetime.now(dt.timezone.utc)
    if row is not None and row.value:
        fetched_at = dt.datetime.fromisoformat(row.value["fetched_at"])
        if (now - fetched_at).total_seconds() < ttl_seconds:
            return float(row.value["score"])
    try:
        items = broker.get_news(symbol, hours=lookback_hours)
        score = score_headlines(items)
    except Exception:                                             # noqa: BLE001
        return 0.0
    value: dict[str, Any] = {"score": score, "fetched_at": now.isoformat()}
    if row is None:
        db.add(AppKv(key=key, value=value))
    else:
        row.value = value
    db.flush()
    return score
