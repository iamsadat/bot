"""Personio public job-feed adapter.

Feed: ``GET https://<company>.jobs.personio.com/xml`` (keyless XML).

Each ``<position>`` carries ``name``/``office``/``jobDescriptions`` (a list of
``jobDescription`` name+value HTML blocks). We parse with stdlib ElementTree
(no deps) and strip the HTML to plain text.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.adapters.greenhouse import html_to_text
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_FEED = "https://{company}.jobs.personio.com/xml"
_JOB_URL = "https://{company}.jobs.personio.com/job/{id}"


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


class PersonioSource(JobSource):
    name = "personio"

    def __init__(self, companies: list[str], http: HTTPClient | None = None) -> None:
        if not companies:
            raise ValueError("at least one company slug is required")
        self._companies = list(companies)
        self._http = http or UrllibHTTPClient()

    def search(self, query: dict) -> list[JobPosting]:
        out: list[JobPosting] = []
        for slug in self._companies:
            url = _FEED.format(company=slug)
            try:
                xml = self._http.get_text(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as exc:
                raise SourceUnavailable(f"{url}: bad XML: {exc}") from exc
            display = slug.replace("-", " ").title()
            for pos in root.iter("position"):
                posting = self._pos_to_posting(pos, slug, display)
                if passes_local_filters(posting, query):
                    out.append(posting)
        return out

    @staticmethod
    def _pos_to_posting(pos, slug: str, company: str) -> JobPosting:
        def txt(tag: str) -> str:
            el = pos.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        job_id = txt("id")
        office = txt("office")
        parts = []
        for jd in pos.iter("jobDescription"):
            val = jd.find("value")
            if val is not None and val.text:
                parts.append(html_to_text(val.text))
        jd_text = "\n".join(p for p in parts if p)
        return JobPosting(
            job_id=f"personio:{job_id}",
            source="personio",
            source_id=job_id,
            url=_JOB_URL.format(company=slug, id=job_id),
            title=txt("name"),
            company=company,
            location=office,
            jd_text=jd_text,
            posted_at=_iso(txt("createdAt")) or time.time(),
            remote="remote" in (office + " " + txt("name")).lower(),
            raw={"personio_office": office, "department": txt("department")},
        )
