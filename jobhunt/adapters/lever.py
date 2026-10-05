"""Lever public-postings adapter.

API: ``GET https://api.lever.co/v0/postings/<company>?mode=json``

Returns a JSON *array* of postings (not wrapped). Each posting has a
plain-text JD in ``descriptionPlain`` plus location/team/commitment in
``categories``.
"""

from __future__ import annotations

import time
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable, fetch_boards
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.http import HTTPClient, UrllibHTTPClient
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
        self._cache: list[JobPosting] | None = None

    def _fetch_all(self) -> list[JobPosting]:
        """Every posting on every configured board, fetched once per instance.

        See ``GreenhouseSource._fetch_all`` — same reasoning: no native search,
        one call per (role, location) pair, and one retired slug must not take
        out the whole source.
        """
        postings: list[JobPosting] = []
        failures: list[str] = []
        # Lever resets connections under heavier concurrency.
        urls = {slug: _API.format(company=slug) for slug in self._companies}
        for slug, payload, error in fetch_boards(self._http, urls, workers=4):
            if error:
                # One retired board must not take out the rest. Only a total
                # failure counts as the source being unavailable.
                failures.append(error)
                continue
            if not isinstance(payload, list):
                continue
            display_company = slug.replace("-", " ").title()
            postings.extend(
                self._row_to_posting(row, display_company) for row in payload
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
