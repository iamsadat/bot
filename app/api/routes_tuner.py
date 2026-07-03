"""Nightly auto-tuner: proposal review + manual trigger."""

from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TunerProposal
from ..trading import audit as audit_log
from ..trading import state as state_mod
from ..trading.tuner import run_tuning_pass

router = APIRouter()


@router.get("/tuner/proposals")
def list_proposals(db: Session = Depends(get_db)):
    rows = db.query(TunerProposal).order_by(desc(TunerProposal.ts)).limit(20).all()
    return [
        {
            "id": r.id, "ts": r.ts, "params": r.params, "metrics": r.metrics,
            "applied": r.applied, "applied_at": r.applied_at,
        }
        for r in rows
    ]


@router.post("/tuner/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, db: Session = Depends(get_db)):
    proposal = db.get(TunerProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    st = state_mod.get_or_create(db)
    st.config = {**(st.config or {}), **proposal.params}
    proposal.applied = True
    proposal.applied_at = dt.datetime.now(dt.timezone.utc)
    audit_log.write("tuner_applied", f"tuner proposal #{proposal.id} applied by user",
                    actor="user", db=db)
    db.commit()
    return {"applied": True, "params": proposal.params}


@router.post("/tuner/run")
async def trigger_run():
    asyncio.create_task(asyncio.to_thread(run_tuning_pass))
    return {"started": True}
