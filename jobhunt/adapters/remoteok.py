"""Remote OK job board adapter.

API: ``GET https://remoteok.com/api`` (keyless). Returns a JSON array whose
first element is a legal-notice object with no job fields — skipped here.

Remote OK's ToS requires attributing "Remote OK" as the source with a
followed link back to them wherever these postings are displayed — do not
strip the ``attribution`` field from ``raw``.
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

_API = "https://remoteok.com/api"

ATTRIBUTION = "Remote OK"


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class RemoteOKSource(JobSource):
    name = "remoteok"

    def __init__(self, http: HTTPClient | None = None) -> None:
        self._http = http or UrllibHTTPClient()
        self._cache: list | None = None

    def search(self, query: dict) -> list[JobPosting]:
        # One board fetch per instance (one sweep), then filter locally.
        # Discovery calls search() once per (role, location) pair and this
        # endpoint takes no query, so re-fetching just wasted rate limit.
        if self._cache is None:
            try:
                self._cache = self._http.get_json(_API)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
        payload = self._cache
        out: list[JobPosting] = []
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, dict) or not row.get("position") or not row.get("id"):
                continue
            posting = self._row_to_posting(row)
            if passes_local_filters(posting, query):
                out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any]) -> JobPosting:
        jd = html_to_text(row.get("description", "") or "")
        return JobPosting(
            job_id=f"remoteok:{row.get('id')}",
            source="remoteok",
            source_id=str(row.get("id", "")),
            url=row.get("url") or row.get("apply_url") or "",
            title=row.get("position", ""),
            company=row.get("company", ""),
            location=row.get("location", ""),
            jd_text=jd,
            posted_at=_iso(row.get("date")) or time.time(),
            salary_min=row.get("salary_min"),
            salary_max=row.get("salary_max"),
            remote=True,
            raw={"remoteok": row, "attribution": ATTRIBUTION},
        )
