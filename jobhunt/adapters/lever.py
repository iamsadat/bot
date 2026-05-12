"""Lever public-postings adapter.

API: ``GET https://api.lever.co/v0/postings/<company>?mode=json``

Returns a JSON *array* of postings (not wrapped). Each posting has a
plain-text JD in ``descriptionPlain`` plus location/team/commitment in
``categories``.
"""

from __future__ import annotations

import time
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://api.lever.co/v0/postings/{company}?mode=json"


class LeverSource(JobSource):
    name = "lever"

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
            if not isinstance(payload, list):
                continue
            display_company = slug.replace("-", " ").title()
            for row in payload:
                posting = self._row_to_posting(row, display_company)
                if passes_local_filters(posting, query):
                    out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any], company: str) -> JobPosting:
        title = row.get("text", "")
        url = row.get("hostedUrl", "")
        cats = row.get("categories") or {}
        location = cats.get("location", "") or ""
        jd = row.get("descriptionPlain", "") or ""
        extra = row.get("additionalPlain", "") or ""
        full_jd = (jd + "\n" + extra).strip()
        created_ms = row.get("createdAt")
        posted_at = float(created_ms) / 1000 if created_ms else time.time()
        return JobPosting(
            job_id=f"lever:{row.get('id')}",
            source="lever",
            source_id=str(row.get("id", "")),
            url=url,
            title=title,
            company=company,
            location=location,
            jd_text=full_jd,
            posted_at=posted_at,
            remote="remote" in location.lower(),
            raw={"lever": row},
        )
