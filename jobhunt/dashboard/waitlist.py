"""SQLite-backed waitlist + pricing-preference signups (Phase 2 validation).

Mirrors ``jobhunt/dashboard/pageviews.py``'s style: tiny declarative-base
table, shared across the app, ``db_url`` param reusing ``build_engine``.
Whole point is a cheap signal of willingness-to-pay before billing is real.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from jobhunt.dashboard.persistence import build_engine

_Base = declarative_base()

#: The price points being tested. Not a pricing decision — just what's on
#: the landing page so signups carry a stated preference.
PRICE_PREFS = ("monthly_19", "monthly_29", "lifetime_99", "lifetime_149")


class _WaitlistEntry(_Base):
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    price_pref = Column(String(20), nullable=False)
    day = Column(String(10), nullable=False)  # ISO date


class WaitlistStore:
    def __init__(
        self, db_path: str | Path = "jobhunt_waitlist.db", db_url: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.engine = build_engine(db_path, db_url)
        _Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def join(self, email: str, price_pref: str, day: str) -> None:
        """Upsert by email — resubmitting just updates the stated preference."""
        with self.Session() as s:
            row = s.query(_WaitlistEntry).filter_by(email=email).one_or_none()
            if row is None:
                row = _WaitlistEntry(email=email)
                s.add(row)
            row.price_pref = price_pref
            row.day = day
            s.commit()

    def counts(self) -> dict:
        with self.Session() as s:
            rows = s.query(_WaitlistEntry).all()
        by_pref = Counter(r.price_pref for r in rows)
        return {
            "total": len(rows),
            "by_price_pref": {p: by_pref.get(p, 0) for p in PRICE_PREFS},
        }
