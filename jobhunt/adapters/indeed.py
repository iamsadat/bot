"""Indeed RSS feed adapter.

Indeed publishes per-query RSS feeds at::

    https://www.indeed.com/rss?q=<role>&l=<location>

The adapter fetches one feed per query dict (``{role, location}``),
parses the RSS with stdlib ``xml.etree.ElementTree``, and returns
:class:`~jobhunt.models.JobPosting` objects that pass the local filters.

No external dependencies are used.  RSS is plain XML / text, so this
adapter uses ``client.get_text`` rather than ``client.get_json``.
"""

from __future__ import annotations

import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.filters import passes_local_filters
from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient
from jobhunt.models import JobPosting

_RSS_URL = "https://www.indeed.com/rss?q={q}&l={l}"


def _build_url(role: str, location: str) -> str:
    q = urllib.parse.quote_plus(role)
    l = urllib.parse.quote_plus(location)
    return _RSS_URL.format(q=q, l=l)


def _split_title(title: str) -> tuple[str, str, str]:
    """Split an Indeed RSS title into (job_title, company, location).

    Indeed formats titles as ``"Job Title - Company - Location"``.  If
    there are fewer than 3 dash-separated parts, fall back gracefully.
    """
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) >= 3:
        return parts[0], parts[1], " - ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return title, "Unknown", ""


def _parse_pub_date(pub_date_str: str | None) -> float:
    """Parse an RFC 822 date string to a POSIX timestamp.

    Falls back to ``time.time()`` on any parse failure.
    """
    if not pub_date_str:
        return time.time()
    try:
        return parsedate_to_datetime(pub_date_str).timestamp()
    except Exception:
        return time.time()


def _parse_item(item: ET.Element) -> dict[str, Any]:
    """Extract raw fields from a single RSS <item> element."""

    def _text(tag: str) -> str:
        el = item.find(tag)
        return (el.text or "") if el is not None else ""

    return {
        "title": _text("title"),
        "link": _text("link"),
        "description": _text("description"),
        "pubDate": _text("pubDate"),
        "guid": _text("guid"),
    }


class IndeedSource(JobSource):
    """Job-board adapter that reads Indeed's public RSS feeds.

    Parameters
    ----------
    queries:
        Pre-computed list of ``{role, location}`` dicts.  When provided,
        ``search()`` ignores its ``query`` argument and fans out over
        every entry in this list.  When *None* (the default), the dict
        passed to ``search()`` is used as-is.
    http:
        Injectable HTTP client.  Defaults to :class:`~jobhunt.http.UrllibHTTPClient`.
    default_query:
        Fallback query dict merged under the ``search()`` argument when
        fields are missing.
    """

    name = "indeed"

    def __init__(
        self,
        queries: list[dict] | None = None,
        http: HTTPClient | None = None,
        default_query: dict | None = None,
    ) -> None:
        self._queries = queries
        self._http = http or UrllibHTTPClient()
        self._default_query = default_query or {}

    # ------------------------------------------------------------------

    def search(self, query: dict) -> list[JobPosting]:
        # Base query: default_query overridden by the caller's query.
        base_query = {**self._default_query, **query}

        if self._queries is not None:
            # Precomputed queries drive both URL construction and filtering.
            # Each entry is merged on top of base_query so that extra flags
            # (e.g. exclude_companies, remote_ok) from the caller still apply.
            targets = [{**base_query, **q} for q in self._queries]
        else:
            targets = [base_query]

        out: list[JobPosting] = []
        for q in targets:
            role = q.get("role", "")
            location = q.get("location", "")
            url = _build_url(role, location)
            try:
                xml_text = self._http.get_text(url)
            except HTTPClientError as exc:
                raise SourceUnavailable(str(exc)) from exc

            postings = self._parse_rss(xml_text, q)
            out.extend(postings)
        return out

    # ------------------------------------------------------------------

    def _parse_rss(self, xml_text: str, query: dict) -> list[JobPosting]:
        """Parse an Indeed RSS feed and return filtered :class:`JobPosting` objects."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        # RSS structure: <rss><channel><item>...</item></channel></rss>
        channel = root.find("channel")
        if channel is None:
            return []

        out: list[JobPosting] = []
        for item_el in channel.findall("item"):
            try:
                posting = self._item_to_posting(item_el)
            except Exception:
                # Malformed item — skip rather than crash.
                continue
            if posting is not None and passes_local_filters(posting, query):
                out.append(posting)
        return out

    def _item_to_posting(self, item_el: ET.Element) -> JobPosting | None:
        raw = _parse_item(item_el)

        title_raw = raw["title"]
        if not title_raw:
            return None

        job_title, company, loc_from_title = _split_title(title_raw)

        # Use the location from the title as a fallback; description may
        # contain more detail but we keep it simple.
        location = loc_from_title

        description = raw["description"]
        link = raw["link"]
        pub_date = raw["pubDate"]
        guid = raw["guid"] or link

        posted_at = _parse_pub_date(pub_date)

        remote = (
            "remote" in job_title.lower()
            or "remote" in location.lower()
            or "remote" in description.lower()
        )

        return JobPosting(
            job_id=f"indeed:{guid}",
            source="indeed",
            source_id=guid,
            url=link,
            title=job_title,
            company=company,
            location=location,
            jd_text=description,
            posted_at=posted_at,
            remote=remote,
            raw={"indeed": raw},
        )
