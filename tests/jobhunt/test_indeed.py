"""Offline tests for the Indeed RSS adapter.

The RSS fixture is defined inline as a string constant — no network access,
no fixture files.
"""

from __future__ import annotations

import pytest

from jobhunt.adapters.base import SourceUnavailable
from jobhunt.adapters.indeed import IndeedSource, _split_title
from jobhunt.http import FakeHTTPClient, HTTPClientError

# ---------------------------------------------------------------------------
# Inline RSS fixture
# ---------------------------------------------------------------------------

_RSS_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Indeed Job Search: backend engineer in San Francisco</title>
    <link>https://www.indeed.com/jobs?q=backend+engineer&amp;l=San+Francisco</link>
    <description>Latest backend engineer jobs in San Francisco</description>

    <item>
      <title>Senior Backend Engineer - Acme Corp - San Francisco, CA</title>
      <link>https://www.indeed.com/viewjob?jk=aaa111</link>
      <description>We are looking for a Senior Backend Engineer to join our team. Python, Kubernetes, distributed systems.</description>
      <pubDate>Mon, 10 Jun 2024 12:00:00 GMT</pubDate>
      <guid>aaa111</guid>
    </item>

    <item>
      <title>Frontend Developer - Widget Inc - Remote</title>
      <link>https://www.indeed.com/viewjob?jk=bbb222</link>
      <description>Frontend role using React and TypeScript.</description>
      <pubDate>Tue, 11 Jun 2024 09:00:00 GMT</pubDate>
      <guid>bbb222</guid>
    </item>

    <item>
      <title>Staff Backend Engineer - Remote Work - Anywhere</title>
      <link>https://www.indeed.com/viewjob?jk=ccc333</link>
      <description>Staff level backend engineering role. Fully remote. Python expertise required.</description>
      <pubDate>Wed, 12 Jun 2024 08:30:00 GMT</pubDate>
      <guid>ccc333</guid>
    </item>

    <item>
      <title>Sales Manager - Sales Corp - Chicago, IL</title>
      <link>https://www.indeed.com/viewjob?jk=ddd444</link>
      <description>Drive sales growth and manage a team of account executives.</description>
      <pubDate>Thu, 13 Jun 2024 14:00:00 GMT</pubDate>
      <guid>ddd444</guid>
    </item>

  </channel>
</rss>
"""

_EMPTY_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Indeed Job Search</title>
    <link>https://www.indeed.com/</link>
    <description>No results</description>
  </channel>
</rss>
"""

_MALFORMED_RSS = "<<NOT XML AT ALL>>"

_RSS_WITH_MALFORMED_ITEM = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Indeed</title>
    <link>https://www.indeed.com/</link>
    <description>test</description>

    <item>
      <!-- item with no title — should be skipped -->
      <link>https://www.indeed.com/viewjob?jk=nnn000</link>
      <description>No title here.</description>
      <pubDate>Fri, 14 Jun 2024 10:00:00 GMT</pubDate>
      <guid>nnn000</guid>
    </item>

    <item>
      <title>Backend Engineer - Good Corp - Austin, TX</title>
      <link>https://www.indeed.com/viewjob?jk=ggg999</link>
      <description>A solid backend engineering role in Austin.</description>
      <pubDate>Fri, 14 Jun 2024 10:00:00 GMT</pubDate>
      <guid>ggg999</guid>
    </item>

  </channel>
</rss>
"""

_INDEED_URL = "https://www.indeed.com/rss?q=backend+engineer&l=San+Francisco"
_EMPTY_URL = "https://www.indeed.com/rss?q=&l="


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_source(url: str, xml: str) -> IndeedSource:
    http = FakeHTTPClient(text_routes={url: xml})
    return IndeedSource(http=http)


# ---------------------------------------------------------------------------
# _split_title unit tests
# ---------------------------------------------------------------------------

def test_split_title_three_parts():
    job, company, location = _split_title("Senior Backend Engineer - Acme Corp - San Francisco, CA")
    assert job == "Senior Backend Engineer"
    assert company == "Acme Corp"
    assert location == "San Francisco, CA"


def test_split_title_two_parts_fallback():
    job, company, location = _split_title("Backend Engineer - Acme Corp")
    assert job == "Backend Engineer"
    assert company == "Acme Corp"
    assert location == ""


def test_split_title_one_part_fallback():
    job, company, location = _split_title("SomeTitle")
    assert job == "SomeTitle"
    assert company == "Unknown"
    assert location == ""


# ---------------------------------------------------------------------------
# IndeedSource.search tests
# ---------------------------------------------------------------------------

def test_indeed_parses_title_company_location():
    """The adapter correctly extracts job title, company, and location."""
    src = _make_source(_INDEED_URL, _RSS_FIXTURE)
    results = src.search({"role": "backend engineer", "location": "San Francisco"})

    titles = {p.title for p in results}
    assert "Senior Backend Engineer" in titles

    senior = next(p for p in results if p.title == "Senior Backend Engineer")
    assert senior.company == "Acme Corp"
    assert senior.location == "San Francisco, CA"


def test_indeed_parses_posted_at():
    """pubDate is converted to a POSIX timestamp."""
    src = _make_source(_INDEED_URL, _RSS_FIXTURE)
    results = src.search({"role": "backend engineer", "location": "San Francisco"})

    senior = next(p for p in results if p.title == "Senior Backend Engineer")
    # 2024-06-10 12:00:00 UTC → 1718013600.0
    assert senior.posted_at is not None
    assert 1_717_000_000 < senior.posted_at < 1_720_000_000


def test_indeed_remote_detection_from_location():
    """'remote' in the title/location triggers remote=True."""
    # Use a URL that matches the empty-role/empty-location query.
    url = "https://www.indeed.com/rss?q=&l="
    http = FakeHTTPClient(text_routes={url: _RSS_FIXTURE})
    src = IndeedSource(http=http)
    results = src.search({"role": "", "location": "", "remote_ok": True})

    remote_roles = [p for p in results if p.remote]
    remote_titles = {p.title for p in remote_roles}

    # "Frontend Developer" is in "Remote" location — should be flagged remote.
    assert "Frontend Developer" in remote_titles
    # "Staff Backend Engineer - Remote Work" — 'remote' is in the title.
    assert any("Staff Backend" in t for t in remote_titles)


def test_indeed_remote_detection_from_title():
    """'remote' appearing in the RSS item title triggers remote=True."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Indeed</title>
    <link>https://www.indeed.com/</link>
    <description/>
    <item>
      <title>Remote Backend Engineer - StartupCo - Anywhere</title>
      <link>https://www.indeed.com/viewjob?jk=r1</link>
      <description>Fully remote position.</description>
      <pubDate>Mon, 10 Jun 2024 12:00:00 GMT</pubDate>
      <guid>r1</guid>
    </item>
  </channel>
</rss>
"""
    url = "https://www.indeed.com/rss?q=backend+engineer&l="
    http = FakeHTTPClient(text_routes={url: xml})
    src = IndeedSource(http=http)
    results = src.search({"role": "backend engineer", "location": ""})

    assert len(results) == 1
    assert results[0].remote is True


def test_indeed_passes_local_filters_role():
    """Only jobs whose title/jd matches the role filter are returned."""
    # URL matches role="backend engineer", location="" (empty location).
    url = "https://www.indeed.com/rss?q=backend+engineer&l="
    http = FakeHTTPClient(text_routes={url: _RSS_FIXTURE})
    src = IndeedSource(http=http)
    # "Sales Manager" is in the fixture but should not match role=backend engineer.
    results = src.search({"role": "backend engineer", "location": ""})

    titles = {p.title for p in results}
    assert "Sales Manager" not in titles
    assert "Senior Backend Engineer" in titles


def test_indeed_passes_local_filters_location():
    """Location filter restricts results, allowing remote when remote_ok=True."""
    # URL matches role="" (empty), location="San Francisco".
    url = "https://www.indeed.com/rss?q=&l=San+Francisco"
    http = FakeHTTPClient(text_routes={url: _RSS_FIXTURE})
    src = IndeedSource(http=http)
    results = src.search(
        {"role": "", "location": "San Francisco", "remote_ok": True}
    )

    for posting in results:
        is_sf = "san francisco" in posting.location.lower()
        is_remote = posting.remote
        assert is_sf or is_remote, (
            f"posting {posting.title!r} passed but is not SF or remote"
        )


def test_indeed_empty_feed_returns_empty_list():
    """An RSS feed with no <item> elements returns an empty list."""
    http = FakeHTTPClient(text_routes={_EMPTY_URL: _EMPTY_RSS})
    src = IndeedSource(http=http)
    results = src.search({"role": "", "location": ""})
    assert results == []


def test_indeed_malformed_item_skipped_good_item_included():
    """A malformed item (missing title) is skipped; valid items are still returned."""
    url = "https://www.indeed.com/rss?q=backend+engineer&l="
    http = FakeHTTPClient(text_routes={url: _RSS_WITH_MALFORMED_ITEM})
    src = IndeedSource(http=http)
    results = src.search({"role": "backend engineer", "location": ""})

    # The good item must be included.
    assert any(p.title == "Backend Engineer" for p in results)
    # No item with an empty title should appear.
    assert all(p.title for p in results)


def test_indeed_malformed_rss_returns_empty_list():
    """Completely invalid XML returns an empty list without raising."""
    url = "https://www.indeed.com/rss?q=&l="
    http = FakeHTTPClient(text_routes={url: _MALFORMED_RSS})
    src = IndeedSource(http=http)
    results = src.search({"role": "", "location": ""})
    assert results == []


def test_indeed_source_unavailable_on_transport_error():
    """HTTPClientError from the HTTP client is re-raised as SourceUnavailable."""

    def boom():
        raise HTTPClientError("connection refused")

    http = FakeHTTPClient(text_routes={_INDEED_URL: boom})
    src = IndeedSource(http=http)

    with pytest.raises(SourceUnavailable, match="connection refused"):
        src.search({"role": "backend engineer", "location": "San Francisco"})


def test_indeed_precomputed_queries_override_search_query():
    """When ``queries`` is set, the adapter fans out over those queries, not the search arg."""
    url_a = "https://www.indeed.com/rss?q=backend+engineer&l=New+York"
    url_b = "https://www.indeed.com/rss?q=data+engineer&l=Remote"

    fixture_a = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Indeed</title><link>https://www.indeed.com/</link><description/>
  <item>
    <title>Backend Engineer - CorpA - New York</title>
    <link>https://www.indeed.com/viewjob?jk=qa1</link>
    <description>Backend engineering in NY.</description>
    <pubDate>Mon, 10 Jun 2024 12:00:00 GMT</pubDate>
    <guid>qa1</guid>
  </item>
</channel></rss>
"""
    fixture_b = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Indeed</title><link>https://www.indeed.com/</link><description/>
  <item>
    <title>Data Engineer - CorpB - Remote</title>
    <link>https://www.indeed.com/viewjob?jk=qb1</link>
    <description>Data engineering role, fully remote.</description>
    <pubDate>Tue, 11 Jun 2024 09:00:00 GMT</pubDate>
    <guid>qb1</guid>
  </item>
</channel></rss>
"""
    http = FakeHTTPClient(text_routes={url_a: fixture_a, url_b: fixture_b})
    src = IndeedSource(
        queries=[
            {"role": "backend engineer", "location": "New York"},
            {"role": "data engineer", "location": "Remote"},
        ],
        http=http,
    )
    results = src.search({"role": "ignored", "location": "ignored"})

    titles = {p.title for p in results}
    assert "Backend Engineer" in titles
    assert "Data Engineer" in titles
