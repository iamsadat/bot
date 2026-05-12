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

    def search(self, query: dict) -> list[JobPosting]:
        out: list[JobPosting] = []
        for slug in self._companies:
            url = _API.format(company=slug)
            try:
                payload = self._http.get_json(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
            display = slug.replace("-", " ").title()
            for row in payload.get("jobs", []):
                posting = self._row_to_posting(row, display)
                if passes_local_filters(posting, query):
                    out.append(posting)
        return out

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
