"""SQLite-backed, privacy-friendly pageview counter for top-of-funnel surfaces.

JobHunt's three public, unauthenticated, no-signup surfaces (landing page,
free ATS-score tool, published résumé pages) have no analytics at all. This
module adds the minimum needed to know whether a single human has ever
visited them — without becoming a tracking log. By construction (not just
policy) we never store IP addresses, user agents, query strings, referrers,
or timestamps finer than a day: a curious operator reading this table can
only ever learn "N views of surface X on day Y", never "who" or "when,
precisely".

Mirrors ``jobhunt/public_store.py``'s style: a tiny declarative-base table,
shared across the app (no per-workspace boundary — pageviews aren't tied to
any user), ``db_url: str | None = None`` constructor param reusing
``build_engine`` from ``jobhunt.dashboard.persistence``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from jobhunt.dashboard.persistence import build_engine

_Base = declarative_base()

#: The only surfaces this store will record. Kept here (not just validated at
#: the API layer) so any caller of ``record`` gets the same guarantee.
SURFACES = ("landing", "ats_tool", "public_resume")

#: How many distinct recent days to include in ``counts()``'s ``by_day`` —
#: bounds the response size; this data is small but no need to let it grow
#: unbounded as the product ages.
_MAX_DAYS = 30

#: How many top refs to include per surface in ``counts()``.
_MAX_TOP_REFS = 10


class _Pageview(_Base):
    __tablename__ = "pageviews"

    id = Column(Integer, primary_key=True)
    surface = Column(String(20), nullable=False)
    ref = Column(String(128), nullable=True)
    day = Column(String(10), nullable=False)  # ISO date, e.g. "2026-06-30" — no timestamp


class PageviewStore:
    """Coarse-grained pageview counter, shared across the app.

    One physical SQLite file. No per-user/per-workspace boundary — like
    ``PublicProfileStore``, this data has no owner beyond "the product".
    """

    def __init__(
        self, db_path: str | Path = "jobhunt_pageviews.db", db_url: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.engine = build_engine(db_path, db_url)
        _Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record(self, surface: str, ref: str | None, day: str) -> None:
        """Record one view of ``surface`` on ``day`` (an ISO date string).

        ``day`` is computed by the caller (the request handler), not here —
        keeps this store trivially testable with deterministic dates and
        keeps the one "now"-equivalent call at the actual call site.
        """
        with self.Session() as s:
            s.add(_Pageview(surface=surface, ref=ref, day=day))
            s.commit()

    def counts(self, surface: str | None = None) -> dict:
        """Return aggregate counts, optionally scoped to a single ``surface``.

        Shape: ``{"landing": {"total": N, "by_day": {...}}, "ats_tool": {...},
        "public_resume": {"total": N, "by_day": {...}, "top_refs": [...]}}``.
        ``by_day`` only covers the most recent ``_MAX_DAYS`` distinct days
        present in the data, so the response can't grow unbounded over time.
        Small dataset (pre-launch product) — aggregating in Python rather
        than reaching for fancier SQL.
        """
        surfaces = (surface,) if surface else SURFACES
        with self.Session() as s:
            rows = s.query(_Pageview).filter(_Pageview.surface.in_(surfaces)).all()

        result: dict = {srf: _empty_surface(srf) for srf in surfaces}
        by_day_counters: dict[str, Counter] = {srf: Counter() for srf in surfaces}
        ref_counters: dict[str, Counter] = {srf: Counter() for srf in surfaces}

        for row in rows:
            entry = result[row.surface]
            entry["total"] += 1
            by_day_counters[row.surface][row.day] += 1
            if row.ref:
                ref_counters[row.surface][row.ref] += 1

        for srf in surfaces:
            recent_days = sorted(by_day_counters[srf].keys())[-_MAX_DAYS:]
            result[srf]["by_day"] = {d: by_day_counters[srf][d] for d in recent_days}
            if srf == "public_resume":
                top = ref_counters[srf].most_common(_MAX_TOP_REFS)
                result[srf]["top_refs"] = [{"ref": r, "count": c} for r, c in top]

        return result


def _empty_surface(surface: str) -> dict:
    base = {"total": 0, "by_day": {}}
    if surface == "public_resume":
        base["top_refs"] = []
    return base
