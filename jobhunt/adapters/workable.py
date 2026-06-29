"""Workable public widget adapter.

API: ``GET https://apply.workable.com/api/v1/widget/accounts/<account>?details=true``
(keyless). Returns ``{"jobs": [...]}`` with HTML ``description`` + ``location``.
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

_API = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class WorkableSource(JobSource):
    name = "workable"

    def __init__(self, accounts: list[str], http: HTTPClient | None = None) -> None:
        if not accounts:
            raise ValueError("at least one account slug is required")
        self._accounts = list(accounts)
        self._http = http or UrllibHTTPClient()

    def search(self, query: dict) -> list[JobPosting]:
        out: list[JobPosting] = []
        for acct in self._accounts:
            url = _API.format(account=acct)
            try:
                payload = self._http.get_json(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
            display = (payload.get("name") if isinstance(payload, dict) else None) \
                or acct.replace("-", " ").title()
            for row in (payload.get("jobs", []) if isinstance(payload, dict) else []):
                posting = self._row_to_posting(row, display)
                if passes_local_filters(posting, query):
                    out.append(posting)
        return out

    @staticmethod
    def _row_to_posting(row: dict[str, Any], company: str) -> JobPosting:
        loc_obj = row.get("location") or {}
        loc = ", ".join(
            str(loc_obj.get(k)) for k in ("city", "region", "country")
            if loc_obj.get(k)
        ) if isinstance(loc_obj, dict) else str(loc_obj)
        jd = html_to_text(row.get("description", "") or "")
        remote = bool(row.get("telecommuting")) or "remote" in loc.lower()
        return JobPosting(
            job_id=f"workable:{row.get('shortcode') or row.get('id')}",
            source="workable",
            source_id=str(row.get("shortcode") or row.get("id", "")),
            url=row.get("application_url") or row.get("url") or row.get("shortlink") or "",
            title=row.get("title", ""),
            company=row.get("company") or company,
            location=loc,
            jd_text=jd,
            posted_at=_iso(row.get("published_on") or row.get("created_at")) or time.time(),
            remote=remote,
            raw={"workable": row},
        )
