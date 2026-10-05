"""Greenhouse public-board adapter.

API: ``GET https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs``
     ``?content=true``

The ``content`` query param asks for HTML-rendered JD text. We strip
the HTML to plain text via a small ``html.parser`` subclass so we have
no extra dependencies.

The adapter is constructed with a list of ``board_tokens`` (one per
company). ``search(query)`` fans out across them, applies the local
role/location/remote filters (the public API has no native search), and
maps to ``JobPosting``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable, fetch_boards
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.http import HTTPClient, UrllibHTTPClient
from jobhunt.models import JobPosting

_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs):
        if tag in {"li", "p", "br", "div"}:
            self._parts.append("\n")

    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def html_to_text(html: str) -> str:
    parser = _HTMLToText()
    parser.feed(html)
    return parser.text()


def _parse_iso8601(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(
        self,
        board_tokens: list[str],
        http: HTTPClient | None = None,
    ) -> None:
        if not board_tokens:
            raise ValueError("at least one board_token is required")
        self._tokens = list(board_tokens)
        self._http = http or UrllibHTTPClient()
        self._cache: list[JobPosting] | None = None

    def _fetch_all(self) -> list[JobPosting]:
        """Every posting on every configured board, fetched once per instance.

        Discovery calls ``search()`` once per (role, location) pair — a dozen
        times for a typical profile — and this API has no native search, so the
        response is identical every time. Fetching per call meant a dozen
        full-board downloads per board per sweep, which with a seeded list of
        company boards is hundreds of megabyte-scale requests.
        """
        postings: list[JobPosting] = []
        failures: list[str] = []
        urls = {token: _API.format(board=token) for token in self._tokens}
        for token, payload, error in fetch_boards(self._http, urls):
            if error:
                # One retired board must not take out the rest. Only a total
                # failure counts as the source being unavailable.
                failures.append(error)
                continue
            company = token.replace("-", " ").title()
            postings.extend(
                self._row_to_posting(row, company)
                for row in payload.get("jobs", [])
            )
        if failures and len(failures) == len(self._tokens):
            raise SourceUnavailable("; ".join(failures[:3]))
        return postings

    def search(self, query: dict) -> list[JobPosting]:
        if self._cache is None:
            self._cache = self._fetch_all()
        return [p for p in self._cache if passes_local_filters(p, query)]

    @staticmethod
    def _row_to_posting(row: dict[str, Any], company: str) -> JobPosting:
        title = row.get("title", "")
        url = row.get("absolute_url", "")
        loc = (row.get("location") or {}).get("name", "")
        jd_html = row.get("content", "")
        jd = html_to_text(jd_html)
        posted_at = _parse_iso8601(row.get("updated_at")) or time.time()
        return JobPosting(
            job_id=f"gh:{row.get('id')}",
            source="greenhouse",
            source_id=str(row.get("id", "")),
            url=url,
            title=title,
            # The board's own name ("Particle41"), not its slug ("Particle41Llc").
            company=(row.get("company_name") or "").strip() or company,
            location=loc,
            jd_text=jd,
            posted_at=posted_at,
            remote="remote" in loc.lower(),
            raw={"greenhouse": row},
        )
