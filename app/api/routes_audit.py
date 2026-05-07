"""Audit log read endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Audit
from ..schemas import AuditEntry

router = APIRouter()


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(limit: int = 200, kind: str | None = None,
               db: Session = Depends(get_db)):
    q = db.query(Audit).order_by(desc(Audit.ts))
    if kind:
        q = q.filter(Audit.kind == kind)
    rows = q.limit(limit).all()
    return [
        AuditEntry(id=r.id, ts=r.ts, kind=r.kind, actor=r.actor,
                   summary=r.summary, detail=r.detail)
        for r in rows
    ]
