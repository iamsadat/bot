"""Strategy lifecycle: start, stop, configure, status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import StrategyConfigSchema, StrategyStateInfo
from ..trading import audit as audit_log
from ..trading import state as state_mod

router = APIRouter()


def _engine(req: Request):
    return req.app.state.engine


@router.get("/strategy", response_model=StrategyStateInfo)
def get_strategy(db: Session = Depends(get_db)):
    st = state_mod.get_or_create(db)
    cfg = StrategyConfigSchema(**(st.config or {}))
    return StrategyStateInfo(
        running=st.running, mode=st.mode, kill_switch=st.kill_switch,
        halted_today=st.halted_today, halted_reason=st.halted_reason,
        last_tick=st.last_tick, last_decision=st.last_decision, config=cfg,
    )


@router.post("/strategy/start")
async def start_strategy(req: Request, db: Session = Depends(get_db)):
    st = state_mod.get_or_create(db)
    if st.kill_switch:
        raise HTTPException(status_code=423,
                            detail="kill_switch_active — release first")
    db.commit()
    await _engine(req).start()
    return {"running": True}


@router.post("/strategy/stop")
async def stop_strategy(req: Request):
    await _engine(req).stop()
    return {"running": False}


@router.put("/strategy/config", response_model=StrategyConfigSchema)
def update_config(cfg: StrategyConfigSchema, db: Session = Depends(get_db)):
    st = state_mod.get_or_create(db)
    st.config = cfg.model_dump()
    audit_log.write("strategy_config_updated",
                    f"updated config: {st.config}",
                    actor="user", db=db)
    db.commit()
    return cfg
