"""Ashby public job-board adapter.

API: ``GET https://api.ashbyhq.com/posting-api/job-board/<company>``

Returns ``{"jobs": [...]}`` with plain-text JDs in ``descriptionPlain``,
already-resolved ``jobUrl``/``applyUrl``, and ``compensationTierSummary``
strings like ``"$180k-$220k base + equity"`` from which we parse a
salary band.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://api.ashbyhq.com/posting-api/job-board/{company}"

_SALARY_RE = re.compile(
    r"\$?(?P<lo>\d{2,3})k?\s*[-–]\s*\$?(?P<hi>\d{2,3})k?", re.IGNORECASE
)


def _parse_salary_band(s: str | None) -> tuple[int | None, int | None]:
    if not s:
        return None, None
    m = _SALARY_RE.search(s)
    if not m:
        return None, None
    lo, hi = int(m.group("lo")), int(m.group("hi"))
    # Compensation summaries usually use thousands shorthand.
    if lo < 1000:
        lo *= 1000
    if hi < 1000:
        hi *= 1000
    return lo, hi


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        # Accept both "Z" and offset forms.
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class AshbySource(JobSource):
    name = "ashby"

    def __init__(
        self,
        companies: list[str],
        http: HTTPClient | None = None,
    ) -> None:
        if not companies:
            raise ValueError("at least one company slug is required")
        self._companies = list(companies)
        self._http = http or UrllibHTTPClient()
        self._cache: list[JobPosting] | None = None

    def _fetch_all(self) -> list[JobPosting]:
        """Every posting on every configured board, fetched once per instance.

        See ``GreenhouseSource._fetch_all`` — same reasoning: no native search,
        one call per (role, location) pair, and one retired slug must not take
        out the whole source.
        """
        postings: list[JobPosting] = []
        failures: list[str] = []
        for slug in self._companies:
            try:
                payload = self._http.get_json(_API.format(company=slug))
            except HTTPClientError as exc:
                failures.append(f"{slug}: {exc}")
                continue
            display = slug.replace("-", " ").title()
            postings.extend(
                self._row_to_posting(row, display)
                for row in payload.get("jobs", [])
            )
        if failures and len(failures) == len(self._companies):
            raise SourceUnavailable("; ".join(failures[:3]))
        return postings

    def search(self, query: dict) -> list[JobPosting]:
        if self._cache is None:
            self._cache = self._fetch_all()
        return [p for p in self._cache if passes_local_filters(p, query)]

    @staticmethod
    def _row_to_posting(row: dict[str, Any], company: str) -> JobPosting:
        location = row.get("location") or ""
        remote = bool(row.get("isRemote") or "remote" in location.lower())
        salary_min, salary_max = _parse_salary_band(
            row.get("compensationTierSummary")
        )
        posted_at = _parse_iso(row.get("publishedAt")) or time.time()
        return JobPosting(
            job_id=f"ashby:{row.get('id')}",
            source="ashby",
            source_id=str(row.get("id", "")),
            url=row.get("jobUrl", ""),
            title=row.get("title", ""),
            company=company,
            location=location,
            jd_text=row.get("descriptionPlain", "") or "",
            posted_at=posted_at,
            salary_min=salary_min,
            salary_max=salary_max,
            remote=remote,
            raw={"ashby": row},
        )
