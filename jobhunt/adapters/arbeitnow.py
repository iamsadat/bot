"""Arbeitnow job board adapter.

API: ``GET https://www.arbeitnow.com/api/job-board-api`` (keyless).
Returns ``{"data": [...], "links": {"next": ...}, "meta": {...}}``, paginated
via ``?page=N``. ``description`` is HTML; ``created_at`` is a Unix timestamp
string; ``remote`` is a stringly-typed boolean.
"""

from __future__ import annotations

import time
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.adapters.greenhouse import html_to_text
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource(JobSource):
    name = "arbeitnow"

    def __init__(self, page: int = 1, http: HTTPClient | None = None) -> None:
        self._page = page
        self._http = http or UrllibHTTPClient()
        self.has_next = False
        self._cache: dict | None = None

    def search(self, query: dict) -> list[JobPosting]:
        # Discovery fans out one search() per (role, location) pair, but this
        # endpoint takes no query — every call would fetch the identical page.
        # Fetch once per instance (one sweep) and filter locally; repeating it
        # only burned rate limit and tripped the circuit breaker, which showed
        # up as a "degraded" source that was in fact returning jobs fine.
        if self._cache is None:
            url = f"{_API}?page={self._page}"
            try:
                self._cache = self._http.get_json(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
        payload = self._cache
        self.has_next = bool((payload.get("links") or {}).get("next"))
        out: list[JobPosting] = []
        for row in payload.get("data", []):
            posting = self._row_to_posting(row)
            if passes_local_filters(posting, query):
                out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any]) -> JobPosting:
        slug = row.get("slug", "")
        jd = html_to_text(row.get("description", "") or "")
        try:
            posted_at = float(row.get("created_at"))
        except (TypeError, ValueError):
            posted_at = time.time()
        remote = str(row.get("remote", "")).strip().lower() == "true"
        return JobPosting(
            job_id=f"arbeitnow:{slug}",
            source="arbeitnow",
            source_id=slug,
            url=row.get("url", ""),
            title=row.get("title", ""),
            company=row.get("company_name", ""),
            location=row.get("location", ""),
            jd_text=jd,
            posted_at=posted_at,
            remote=remote,
            raw={"arbeitnow": row},
        )
