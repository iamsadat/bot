"""Himalayas remote-job adapter.

API: ``GET https://himalayas.app/jobs/api?limit=N&offset=M`` (keyless). Returns
``{"jobs": [...], "offset": N, "limit": N, "totalCount": N}``. Around 97k
postings, so pagination is the point — ``offset`` walks it.

Two fields make this board worth more than its size: ``locationRestrictions``
(which countries a remote role is actually open to) and ``seniority``. Both feed
gates that previously had to guess from the job title, and the location scope is
what stops an India-based search matching a Germany-only remote role.
"""

from __future__ import annotations

import time
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.adapters.greenhouse import html_to_text
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://himalayas.app/jobs/api"

# The API silently caps at 20 rows per request — asking for 50 or 100 still
# returns 20. Paging by anything larger would skip the difference on every
# advance, so the stride has to match the real cap.
_PAGE_SIZE = 20


class HimalayasSource(JobSource):
    name = "himalayas"

    def __init__(
        self,
        page: int = 1,
        http: HTTPClient | None = None,
        page_size: int = _PAGE_SIZE,
    ) -> None:
        self._page = max(1, page)
        self._page_size = page_size
        self._http = http or UrllibHTTPClient()
        self.has_next = False
        self._cache: dict | None = None

    def search(self, query: dict) -> list[JobPosting]:
        # Fetch once per instance: this endpoint takes no query, and discovery
        # calls search() once per (role, location) pair. See arbeitnow.py.
        if self._cache is None:
            offset = (self._page - 1) * self._page_size
            url = f"{_API}?limit={self._page_size}&offset={offset}"
            try:
                self._cache = self._http.get_json(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
        payload = self._cache
        offset = int(payload.get("offset") or 0)
        limit = int(payload.get("limit") or self._page_size)
        self.has_next = offset + limit < int(payload.get("totalCount") or 0)

        out: list[JobPosting] = []
        for row in payload.get("jobs", []):
            posting = self._row_to_posting(row)
            if passes_local_filters(posting, query):
                out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any]) -> JobPosting:
        restrictions = [str(c) for c in (row.get("locationRestrictions") or [])]
        guid = row.get("guid", "") or ""
        return JobPosting(
            job_id=f"himalayas:{row.get('companySlug', '')}:{row.get('title', '')[:60]}",
            source="himalayas",
            source_id=guid or str(row.get("title", "")),
            url=row.get("applicationLink") or guid,
            title=row.get("title", ""),
            company=row.get("companyName", ""),
            # Every posting here is remote, so the useful "location" is the set
            # of countries it is open to.
            location=", ".join(restrictions) or "Remote",
            jd_text=html_to_text(row.get("description", "") or ""),
            posted_at=float(row.get("pubDate") or 0) or time.time(),
            salary_min=row.get("minSalary"),
            salary_max=row.get("maxSalary"),
            remote=True,
            raw={
                "himalayas": row,
                # Read by adapters/filters._declared_remote_scope and
                # seniority.level_from_posting.
                "remote_scope": restrictions,
                "seniority": [str(s) for s in (row.get("seniority") or [])],
            },
        )
