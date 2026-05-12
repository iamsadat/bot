"""Deterministic fixture-backed adapter used in tests and the demo CLI.

It pretends to be a job source but reads from a JSON fixture, optionally
filtered by the query. Production adapters (Greenhouse/Lever/Ashby) plug
into the same interface; replace the ``_load`` step with an HTTP call
and the rest of the system is unchanged.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.models import JobPosting

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "jobs.json"


class FixtureSource(JobSource):
    """A pluggable, offline JobSource.

    ``name`` is configurable so the demo can simulate several sources
    from the same fixture without making the test rely on network IO.
    ``unavailable`` flips the adapter into raising SourceUnavailable so
    tests can verify the degradation path.
    """

    def __init__(
        self,
        name: str = "fixture",
        path: Path = _FIXTURE,
        unavailable: bool = False,
        only_sources: list[str] | None = None,
    ) -> None:
        self.name = name
        self._path = path
        self._unavailable = unavailable
        self._only_sources = only_sources

    def _load(self) -> list[dict]:
        with self._path.open() as f:
            return json.load(f)

    def search(self, query: dict) -> list[JobPosting]:
        if self._unavailable:
            raise SourceUnavailable(f"{self.name} is down")

        rows = self._load()
        if self._only_sources is not None:
            rows = [r for r in rows if r["source"] in self._only_sources]

        out: list[JobPosting] = []
        for r in rows:
            posted_at = r.get("posted_at")
            if posted_at is None and "posted_days_ago" in r:
                posted_at = time.time() - r["posted_days_ago"] * 86400
            posting = JobPosting(
                job_id=uuid.uuid4().hex,
                source=r["source"],
                source_id=r["source_id"],
                url=r["url"],
                title=r["title"],
                company=r["company"],
                location=r["location"],
                jd_text=r["jd_text"],
                posted_at=posted_at,
                salary_min=r.get("salary_min"),
                salary_max=r.get("salary_max"),
                remote=r.get("remote", False),
                raw=r,
            )
            if passes_local_filters(posting, query):
                out.append(posting)
        return out
