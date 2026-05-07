"""Append-only audit log helper."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Audit

log = logging.getLogger("audit")


def write(kind: str, summary: str, *, actor: str = "system",
          detail: dict[str, Any] | None = None,
          db: Session | None = None) -> None:
    entry = Audit(kind=kind, actor=actor, summary=summary, detail=detail)
    log.info("[%s] %s — %s", actor, kind, summary)
    if db is not None:
        db.add(entry)
        db.flush()
        return
    with session_scope() as s:
        s.add(entry)
