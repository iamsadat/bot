"""Adzuna aggregator adapter (native search; free developer key).

API: ``GET https://api.adzuna.com/v1/api/jobs/<country>/search/1`` with
``app_id``/``app_key`` query params + ``what``/``where`` search terms. Returns
``{"results": [...]}``. Get a free key at https://developer.adzuna.com/.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1?{qs}"


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(
        self, app_id: str, app_key: str, country: str = "us",
        results_per_page: int = 50, http: HTTPClient | None = None,
    ) -> None:
        if not app_id or not app_key:
            raise ValueError("Adzuna app_id and app_key are required")
        self._id = app_id
        self._key = app_key
        self._country = country
        self._rpp = results_per_page
        self._http = http or UrllibHTTPClient()

    def _url(self, query: dict) -> str:
        # Fixed param order → deterministic URL (offline tests key on it).
        params = [
            ("app_id", self._id),
            ("app_key", self._key),
            ("results_per_page", str(self._rpp)),
            ("what", query.get("role", "") or ""),
            ("where", query.get("location", "") or ""),
            ("content-type", "application/json"),
        ]
        return _BASE.format(country=self._country, qs=urlencode(params))

    def search(self, query: dict) -> list[JobPosting]:
        try:
            payload = self._http.get_json(self._url(query))
        except HTTPClientError as exc:
            raise SourceUnavailable(str(exc)) from exc
        excluded = {c.lower() for c in query.get("exclude_companies", [])}
        out: list[JobPosting] = []
        for row in (payload.get("results", []) if isinstance(payload, dict) else []):
            posting = self._row_to_posting(row)
            if posting.company.lower() not in excluded:
                out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any]) -> JobPosting:
        loc = (row.get("location") or {}).get("display_name", "") or ""
        return JobPosting(
            job_id=f"adzuna:{row.get('id')}",
            source="adzuna",
            source_id=str(row.get("id", "")),
            url=row.get("redirect_url", ""),
            title=row.get("title", ""),
            company=(row.get("company") or {}).get("display_name", "") or "",
            location=loc,
            jd_text=row.get("description", "") or "",
            posted_at=_iso(row.get("created")) or time.time(),
            salary_min=_int(row.get("salary_min")),
            salary_max=_int(row.get("salary_max")),
            remote="remote" in loc.lower(),
            raw={"adzuna": row},
        )
