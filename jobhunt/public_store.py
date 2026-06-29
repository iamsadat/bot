"""SQLite-backed store for published, unauthenticated public résumé pages.

Mirrors ``jobhunt/dashboard/persistence.py``'s style: a tiny declarative-base
table holding a JSON blob per row, upserted by a stable string key (here, the
public ``handle`` rather than a single-row snapshot id).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column, DateTime, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_Base = declarative_base()


class _PublicProfile(_Base):
    __tablename__ = "public_profiles"

    handle = Column(String(64), primary_key=True)
    draft_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublicProfileStore:
    """Upsert-by-handle store for published résumé drafts.

    One physical SQLite file, shared across the app — public pages have no
    per-user auth boundary, so a single table keyed by ``handle`` is enough.
    """

    def __init__(self, db_path: str | Path = "jobhunt_public.db") -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        _Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def publish(self, handle: str, draft: dict[str, Any]) -> None:
        """Upsert the published draft for ``handle``."""
        with self.Session() as s:
            row = s.get(_PublicProfile, handle)
            if row is None:
                row = _PublicProfile(handle=handle)
                s.add(row)
            row.draft_json = draft
            row.updated_at = datetime.utcnow()
            s.commit()

    def get(self, handle: str) -> dict[str, Any] | None:
        """Return the published draft dict for ``handle``, or ``None``."""
        with self.Session() as s:
            row = s.get(_PublicProfile, handle)
            if row is None:
                return None
            return dict(row.draft_json or {})
