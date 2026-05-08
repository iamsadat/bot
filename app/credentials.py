"""Credential resolution helper.

Lookup priority:

  1. Environment variables (``ALPACA_PAPER_KEY`` / ``ALPACA_PAPER_SECRET`` etc.)
  2. ``credentials`` table written via the setup UI.

GET endpoints never return raw secrets — they only report which slots are
configured and which are masked.  Only the broker factory ever reads the
plaintext.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from .config import settings
from .db import session_scope
from .models import Credential


def resolve(mode: Literal["paper", "live"]) -> tuple[str | None, str | None, str]:
    """Return (api_key, api_secret, source) for the requested mode."""
    if mode == "paper":
        env_key, env_secret = settings.alpaca_paper_key, settings.alpaca_paper_secret
    else:
        env_key, env_secret = settings.alpaca_live_key, settings.alpaca_live_secret

    if env_key and env_secret:
        return env_key, env_secret, "env"

    with session_scope() as db:
        row = db.get(Credential, mode)
        if row is None:
            return None, None, "none"
        return row.api_key, row.api_secret, "db"


def status() -> dict:
    """Return per-mode configuration status without revealing secrets."""
    out = {}
    for mode in ("paper", "live"):
        key, _, source = resolve(mode)  # type: ignore[arg-type]
        out[mode] = {
            "configured": bool(key),
            "source": source,
            "key_preview": (key[:4] + "…" + key[-4:]) if key and len(key) >= 8 else None,
        }
    return out


def save(db: Session, mode: Literal["paper", "live"],
         api_key: str, api_secret: str) -> None:
    row = db.get(Credential, mode)
    if row is None:
        row = Credential(mode=mode, api_key=api_key, api_secret=api_secret)
        db.add(row)
    else:
        row.api_key = api_key
        row.api_secret = api_secret
    db.flush()


def clear(db: Session, mode: Literal["paper", "live"]) -> None:
    row = db.get(Credential, mode)
    if row is not None:
        db.delete(row)
        db.flush()
