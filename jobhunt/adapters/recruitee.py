"""Recruitee public-careers adapter.

API: ``GET https://<company>.recruitee.com/api/offers/`` (keyless).

Returns ``{"offers": [...]}``; each offer has an HTML ``description`` plus
``careers_url``/``location``. Mirrors the Greenhouse/Lever adapters: inject an
HTTP client for offline tests, fan out across company slugs, filter locally.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.adapters.greenhouse import html_to_text
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://{company}.recruitee.com/api/offers/"


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class RecruiteeSource(JobSource):
    name = "recruitee"

    def __init__(self, companies: list[str], http: HTTPClient | None = None) -> None:
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
            for row in payload.get("offers", []):
                posting = self._row_to_posting(row, display)
                if passes_local_filters(posting, query):
                    out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any], company: str) -> JobPosting:
        loc = row.get("location") or row.get("city") or ""
        jd = html_to_text(row.get("description", "") or "")
        return JobPosting(
            job_id=f"recruitee:{row.get('id')}",
            source="recruitee",
            source_id=str(row.get("id", "")),
            url=row.get("careers_url") or row.get("careers_apply_url") or "",
            title=row.get("title", ""),
            company=row.get("company_name") or company,
            location=loc,
            jd_text=jd,
            posted_at=_iso(row.get("published_at") or row.get("created_at")) or time.time(),
            remote="remote" in str(loc).lower(),
            raw={"recruitee": row},
        )
