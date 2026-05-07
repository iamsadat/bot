"""Safety routes — kill switch, mode change."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import ModeChangeRequest
from ..trading import audit as audit_log
from ..trading import state as state_mod

router = APIRouter()


def _engine(req: Request):
    return req.app.state.engine


@router.post("/safety/kill")
async def kill(req: Request):
    """Cancel everything, flatten everything, halt the engine."""
    await _engine(req).kill()
    return {"kill_switch": True}


@router.post("/safety/release")
async def release(req: Request):
    """Release the kill switch.  Engine remains stopped — you must restart."""
    await _engine(req).reset_kill_switch()
    return {"kill_switch": False}


@router.post("/safety/mode")
async def change_mode(req: ModeChangeRequest, request: Request,
                      db: Session = Depends(get_db)):
    """Switch broker mode.

    Going to *live* requires:
      * the engine to be stopped first,
      * the live API keys to be configured,
      * a confirmation phrase that matches ``settings.live_confirmation_phrase``.
    """
    st = state_mod.get_or_create(db)

    if req.mode == st.mode:
        return {"mode": st.mode, "changed": False}

    if st.running:
        raise HTTPException(status_code=409,
                            detail="stop the engine before changing mode")

    if req.mode == "live":
        if (settings.alpaca_live_key is None
                or settings.alpaca_live_secret is None):
            raise HTTPException(
                status_code=503,
                detail="live keys not configured "
                       "(set ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET)",
            )
        if req.confirmation != settings.live_confirmation_phrase:
            raise HTTPException(
                status_code=400,
                detail=(f"missing confirmation phrase "
                        f"(expected '{settings.live_confirmation_phrase}')"),
            )

    st.mode = req.mode
    audit_log.write("mode_changed", f"mode → {req.mode}",
                    actor="user", db=db)
    db.commit()
    return {"mode": req.mode, "changed": True}
