"""USAJobs aggregator adapter (US federal jobs; free API key + header auth).

API: ``GET https://data.usajobs.gov/api/search`` with ``Keyword``/``LocationName``
search params and header auth (``User-Agent`` = your email, ``Authorization-Key``
= your key). Register at https://developer.usajobs.gov/.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_BASE = "https://data.usajobs.gov/api/search?{qs}"


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
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class USAJobsSource(JobSource):
    name = "usajobs"

    def __init__(
        self, email: str, api_key: str, results_per_page: int = 50,
        http: HTTPClient | None = None,
    ) -> None:
        if not email or not api_key:
            raise ValueError("USAJobs email and api_key are required")
        self._email = email
        self._key = api_key
        self._rpp = results_per_page
        self._http = http or UrllibHTTPClient()

    def _url(self, query: dict) -> str:
        params = [
            ("Keyword", query.get("role", "") or ""),
            ("LocationName", query.get("location", "") or ""),
            ("ResultsPerPage", str(self._rpp)),
        ]
        return _BASE.format(qs=urlencode(params))

    def search(self, query: dict) -> list[JobPosting]:
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": self._email,
            "Authorization-Key": self._key,
        }
        try:
            payload = self._http.get_json(self._url(query), headers=headers)
        except HTTPClientError as exc:
            raise SourceUnavailable(str(exc)) from exc
        items = (
            (payload.get("SearchResult") or {}).get("SearchResultItems", [])
            if isinstance(payload, dict) else []
        )
        excluded = {c.lower() for c in query.get("exclude_companies", [])}
        out: list[JobPosting] = []
        for item in items:
            posting = self._item_to_posting(item)
            if posting.company.lower() not in excluded:
                out.append(posting)
        return out

    @staticmethod
    def _item_to_posting(item: dict[str, Any]) -> JobPosting:
        d = item.get("MatchedObjectDescriptor", {}) or {}
        loc = d.get("PositionLocationDisplay", "") or ""
        summary = ((d.get("UserArea") or {}).get("Details") or {}).get("JobSummary", "")
        rem = (d.get("PositionRemuneration") or [{}])
        rem0 = rem[0] if rem else {}
        return JobPosting(
            job_id=f"usajobs:{item.get('MatchedObjectId')}",
            source="usajobs",
            source_id=str(item.get("MatchedObjectId", "")),
            url=d.get("PositionURI", "") or "",
            title=d.get("PositionTitle", "") or "",
            company=d.get("OrganizationName", "") or "",
            location=loc,
            jd_text=summary or d.get("QualificationSummary", "") or "",
            posted_at=_iso(d.get("PublicationStartDate") or d.get("PositionStartDate")) or time.time(),
            salary_min=_int(rem0.get("MinimumRange")),
            salary_max=_int(rem0.get("MaximumRange")),
            remote="remote" in loc.lower(),
            raw={"usajobs": d},
        )
