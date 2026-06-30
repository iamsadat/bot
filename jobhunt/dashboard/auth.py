"""Minimal email identity layer: magic links that tie a workspace to an email.

JobHunt's only identity mechanism is the anonymous ``jh_ws`` cookie (see
``jobhunt/dashboard/server.py``). If a user clears cookies or switches
devices, their workspace is unreachable. This module adds the minimum
needed to recover it: a verified email maps to a workspace id, and a
short-lived single-use token (the "magic link") proves the requester
controls that email before the mapping is trusted.

Mirrors ``jobhunt/public_store.py``'s style: small SQLAlchemy declarative-base
tables, ``db_url: str | None = None`` constructor param reusing
``build_engine`` from ``jobhunt.dashboard.persistence``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.orm import declarative_base, sessionmaker

from jobhunt.dashboard.persistence import build_engine

_Base = declarative_base()


class _EmailWorkspace(_Base):
    __tablename__ = "auth_email_workspaces"

    email = Column(String(320), primary_key=True)
    ws_id = Column(String(32), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class _MagicToken(_Base):
    __tablename__ = "auth_magic_tokens"

    token = Column(String(64), primary_key=True)
    email = Column(String(320), nullable=False)
    ws_id_hint = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class EmailIdentityStore:
    """SQLite-backed store for email→workspace mapping + magic-link tokens."""

    def __init__(
        self, db_path: str | Path = "jobhunt_auth.db", db_url: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.engine = build_engine(db_path, db_url)
        _Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    # --------------------------------------------------------------- tokens

    def create_token(
        self,
        email: str,
        ws_id_hint: str | None = None,
        ttl_seconds: int = 900,
        *,
        now: datetime | None = None,
    ) -> str:
        """Mint a single-use magic-link token for ``email``, valid ``ttl_seconds``."""
        now = now or datetime.utcnow()
        token = secrets.token_urlsafe(32)
        with self.Session() as s:
            s.add(_MagicToken(
                token=token,
                email=email,
                ws_id_hint=ws_id_hint,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                used=False,
            ))
            s.commit()
        return token

    def consume_token(
        self, token: str, *, now: datetime | None = None,
    ) -> dict | None:
        """Mark ``token`` used and return its payload, or ``None`` if invalid.

        Returns ``None`` when the token is missing, expired, or already used
        (single-use — defense against replay). Otherwise returns
        ``{"email": ..., "ws_id_hint": ...}`` and marks the token consumed.
        """
        now = now or datetime.utcnow()
        with self.Session() as s:
            row = s.get(_MagicToken, token)
            if row is None or row.used or row.expires_at < now:
                return None
            row.used = True
            result = {"email": row.email, "ws_id_hint": row.ws_id_hint}
            s.commit()
            return result

    # ------------------------------------------------------------- mapping

    def get_workspace_for_email(self, email: str) -> str | None:
        """Return the workspace id linked to ``email``, or ``None``."""
        with self.Session() as s:
            row = s.get(_EmailWorkspace, email)
            return row.ws_id if row is not None else None

    def link_email_to_workspace(self, email: str, ws_id: str) -> None:
        """Upsert the email→workspace mapping."""
        with self.Session() as s:
            row = s.get(_EmailWorkspace, email)
            if row is None:
                row = _EmailWorkspace(email=email)
                s.add(row)
            row.ws_id = ws_id
            row.updated_at = datetime.utcnow()
            s.commit()


def send_magic_link_email(
    to_addr: str, link: str, *, send_fn: Callable[..., None] | None = None,
) -> bool:
    """Send a magic-link email. Mirrors ``jobhunt/notify.py``'s ``EmailSink``.

    When ``send_fn`` is given, it is called instead of touching the network
    (offline tests), with the same shape as ``EmailSink``:
    ``send_fn(from_addr, to_addr, body)``. Reads SMTP config from env for the
    real path. Returns ``False`` (never raises) when SMTP isn't configured
    and no ``send_fn`` is given — callers fall back to dev mode in that case.
    """
    import os

    host = os.environ.get("JOBHUNT_SMTP_HOST", "")
    port = int(os.environ.get("JOBHUNT_SMTP_PORT", "587"))
    user = os.environ.get("JOBHUNT_SMTP_USER", "")
    password = os.environ.get("JOBHUNT_SMTP_PASSWORD", "")

    body = (
        f"Subject: JobHunt: sign in to your workspace\n\n"
        f"Click to verify your email and access your JobHunt workspace:\n{link}\n\n"
        f"This link expires soon and can only be used once. If you didn't "
        f"request this, you can ignore this email."
    ).strip()

    if send_fn is not None:
        send_fn(user, to_addr, body)
        return True

    if not host:
        return False

    import smtplib  # stdlib; only imported on real send
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls()
        s.login(user, password)
        s.sendmail(user, [to_addr], body)
    return True
