"""Connection setup routes.

  * GET  /api/setup/status        — which credential slots are configured
  * POST /api/setup/test          — try a credential pair against Alpaca
  * POST /api/setup/keys          — persist a credential pair (after testing)
  * POST /api/setup/clear         — wipe a credential pair
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import credentials as creds
from ..brokers import AlpacaBroker
from ..brokers.base import BrokerError, NotConfiguredError
from ..db import get_db
from ..trading import audit as audit_log

router = APIRouter()


class KeyPayload(BaseModel):
    mode: Literal["paper", "live"]
    api_key: str
    api_secret: str


class ModePayload(BaseModel):
    mode: Literal["paper", "live"]


@router.get("/setup/status")
def get_status():
    return creds.status()


@router.post("/setup/test")
def test_keys(p: KeyPayload):
    """Run a single read-only call against Alpaca with the given keys.

    Does not persist anything.  Returns the account snapshot on success so
    the UI can display the resolved equity / mode.
    """
    broker = AlpacaBroker(api_key=p.api_key, secret_key=p.api_secret, mode=p.mode)
    try:
        snap = broker.get_account()
    except NotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=f"connection failed: {e}")
    return {
        "ok": True,
        "mode": p.mode,
        "equity": snap.equity,
        "cash": snap.cash,
        "buying_power": snap.buying_power,
    }


@router.post("/setup/keys")
def save_keys(p: KeyPayload, db: Session = Depends(get_db)):
    # Test first; never persist a non-working pair.
    broker = AlpacaBroker(api_key=p.api_key, secret_key=p.api_secret, mode=p.mode)
    try:
        broker.get_account()
    except NotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=f"connection failed: {e}")

    creds.save(db, p.mode, p.api_key, p.api_secret)
    audit_log.write(
        "credentials_saved",
        f"saved {p.mode} keys via setup UI",
        actor="user",
        detail={"mode": p.mode, "key_preview": p.api_key[:4] + "…"},
        db=db,
    )
    db.commit()
    return {"ok": True, "mode": p.mode}


@router.post("/setup/clear")
def clear(p: ModePayload, db: Session = Depends(get_db)):
    creds.clear(db, p.mode)
    audit_log.write(
        "credentials_cleared",
        f"cleared {p.mode} keys",
        actor="user", db=db,
    )
    db.commit()
    return {"ok": True, "mode": p.mode}
