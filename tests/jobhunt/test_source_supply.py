"""The sources a new workspace actually reads, against recorded payloads.

Payload shapes are copied from live responses. Nothing here touches the network:
the autouse fixture in ``conftest.py`` pins ``JOBHUNT_OFFLINE=1`` and every
adapter takes an injectable HTTP client.
"""

from __future__ import annotations

import pytest

from jobhunt import company_boards
from jobhunt.adapters.adzuna import AdzunaSource, country_for
from jobhunt.adapters.greenhouse import GreenhouseSource
from jobhunt.adapters.himalayas import HimalayasSource
from jobhunt.adapters.lever import LeverSource
from jobhunt.http import FakeHTTPClient, HTTPClientError
from jobhunt.seniority import JUNIOR, level_from_posting

HIMALAYAS_URL = "https://himalayas.app/jobs/api?limit=20&offset=0"

HIMALAYAS_PAYLOAD = {
    "offset": 0,
    "limit": 20,
    "totalCount": 98512,
    "jobs": [
        {
            "title": "Junior Data Engineer",
            "companyName": "Acme",
            "companySlug": "acme",
            "description": "<p>Build ETL pipelines with PySpark and Databricks.</p>",
            "guid": "https://himalayas.app/companies/acme/jobs/junior-data-engineer",
            "applicationLink": "https://acme.example/apply",
            "locationRestrictions": ["India"],
            "seniority": ["Entry-level", "Mid-level"],
            "pubDate": 1785388450,
            "minSalary": 1000000,
            "maxSalary": 1800000,
        },
        {
            "title": "Staff Data Engineer",
            "companyName": "Globex",
            "companySlug": "globex",
            "description": "<p>Lead our data platform.</p>",
            "guid": "https://himalayas.app/companies/globex/jobs/staff",
            "locationRestrictions": ["United States"],
            "seniority": ["Senior"],
            "pubDate": 1785388450,
        },
    ],
}

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 1,
            "title": "Data Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/phonepe/jobs/1",
            "location": {"name": "Bengaluru, India"},
            "content": "Build pipelines with PySpark, SQL and Airflow.",
            "updated_at": "2026-07-01T00:00:00+00:00",
        }
    ]
}

QUERY = {
    "role": "data engineer", "location": "hyderabad", "remote_ok": True,
    "exclude_companies": [], "candidate_level": JUNIOR,
    "all_locations": ["Hyderabad", "India", "Remote"],
}


# --------------------------------------------------------------------------- #
# Himalayas
# --------------------------------------------------------------------------- #

def test_himalayas_maps_seniority_and_remote_scope():
    http = FakeHTTPClient({HIMALAYAS_URL: HIMALAYAS_PAYLOAD})
    postings = HimalayasSource(page=1, http=http).search({})
    assert len(postings) == 2
    junior = postings[0]
    assert junior.raw["remote_scope"] == ["India"]
    # Open to several levels means reachable at the lowest of them.
    assert level_from_posting(junior) == JUNIOR


def test_himalayas_filters_by_declared_scope_and_level():
    http = FakeHTTPClient({HIMALAYAS_URL: HIMALAYAS_PAYLOAD})
    postings = HimalayasSource(page=1, http=http).search(QUERY)
    assert [p.title for p in postings] == ["Junior Data Engineer"]


def test_himalayas_reports_more_pages():
    http = FakeHTTPClient({HIMALAYAS_URL: HIMALAYAS_PAYLOAD})
    src = HimalayasSource(page=1, http=http)
    src.search({})
    assert src.has_next is True


def test_himalayas_pages_by_the_apis_real_cap():
    """The API silently returns 20 rows however large a limit is asked for, so
    striding by anything else would skip postings on every advance."""
    http = FakeHTTPClient({
        "https://himalayas.app/jobs/api?limit=20&offset=20": {
            "offset": 20, "limit": 20, "totalCount": 30, "jobs": [],
        }
    })
    src = HimalayasSource(page=2, http=http)
    src.search({})
    assert http.calls == ["https://himalayas.app/jobs/api?limit=20&offset=20"]
    assert src.has_next is False


def test_himalayas_unavailable_is_reported_not_raised_as_http_error():
    def boom():
        raise HTTPClientError("503 Service Unavailable")

    http = FakeHTTPClient({HIMALAYAS_URL: boom})
    from jobhunt.adapters.base import SourceUnavailable

    with pytest.raises(SourceUnavailable):
        HimalayasSource(page=1, http=http).search({})


# --------------------------------------------------------------------------- #
# Board adapters: one fetch per sweep, and one dead board is survivable
# --------------------------------------------------------------------------- #

def _gh_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def test_board_is_fetched_once_per_instance_not_once_per_query():
    """Discovery calls search() once per (role, location) pair. Re-fetching per
    call meant hundreds of full-board downloads per sweep with a seeded list."""
    http = FakeHTTPClient({_gh_url("phonepe"): GREENHOUSE_PAYLOAD})
    src = GreenhouseSource(board_tokens=["phonepe"], http=http)
    for _ in range(5):
        src.search(QUERY)
    assert len(http.calls) == 1


def test_one_dead_board_does_not_take_out_the_others():
    def gone():
        raise HTTPClientError("404 Not Found")

    http = FakeHTTPClient({
        _gh_url("retired"): gone,
        _gh_url("phonepe"): GREENHOUSE_PAYLOAD,
    })
    src = GreenhouseSource(board_tokens=["retired", "phonepe"], http=http)
    assert [p.company for p in src.search(QUERY)] == ["Phonepe"]


def test_all_boards_dead_is_a_source_failure():
    def gone():
        raise HTTPClientError("404 Not Found")

    from jobhunt.adapters.base import SourceUnavailable

    http = FakeHTTPClient({_gh_url("a"): gone, _gh_url("b"): gone})
    with pytest.raises(SourceUnavailable):
        GreenhouseSource(board_tokens=["a", "b"], http=http).search(QUERY)


def test_lever_caches_and_tolerates_a_dead_slug():
    def gone():
        raise HTTPClientError("404 Not Found")

    rows = [{
        "text": "Data Engineer",
        "hostedUrl": "https://jobs.lever.co/meesho/1",
        "categories": {"location": "Bangalore, India"},
        "descriptionPlain": "PySpark, SQL, Airflow pipelines.",
        "createdAt": 1750000000000,
    }]
    http = FakeHTTPClient({
        "https://api.lever.co/v0/postings/meesho?mode=json": rows,
        "https://api.lever.co/v0/postings/gone?mode=json": gone,
    })
    src = LeverSource(companies=["gone", "meesho"], http=http)
    src.search(QUERY)
    src.search(QUERY)
    assert [p.title for p in src.search(QUERY)] == ["Data Engineer"]
    assert len(http.calls) == 2  # one per slug, once — not once per search


# --------------------------------------------------------------------------- #
# Seeded company boards
# --------------------------------------------------------------------------- #

def test_seeded_boards_are_india_weighted_and_paged():
    first = company_boards.page(1)
    assert first["greenhouse"], first
    assert len(first["greenhouse"]) == company_boards.BOARDS_PER_PAGE
    # A later sweep reads different companies, which is what makes repeat
    # "Fetch more" clicks return new jobs rather than the same ones.
    assert company_boards.page(2)["greenhouse"] != first["greenhouse"]


def test_seeded_board_paging_wraps_instead_of_going_quiet():
    last = company_boards.total_pages()
    assert company_boards.has_next(last) is False
    assert company_boards.page(last + 1)["greenhouse"] == company_boards.page(1)["greenhouse"]


def test_every_seeded_slug_is_unique():
    for group in (company_boards.GREENHOUSE, company_boards.LEVER, company_boards.ASHBY):
        assert len(group) == len(set(group)), group


# --------------------------------------------------------------------------- #
# Adzuna
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("locations,expected", [
    (["Hyderabad", "India", "Remote"], "in"),
    (["Bengaluru"], "in"),
    (["London"], "gb"),
    (["Seattle"], "us"),
    # Nothing recognisable: fall back rather than guess.
    (["Remote"], "us"),
    ([], "us"),
])
def test_adzuna_country_comes_from_the_profile(locations, expected):
    assert country_for(locations) == expected


def test_adzuna_asks_for_fresh_results_and_a_page():
    http = FakeHTTPClient()
    src = AdzunaSource(app_id="id", app_key="key", country="in", page=2, http=http)
    url = src._url({"role": "data engineer", "location": "Hyderabad"})
    assert "/jobs/in/search/2?" in url
    assert "max_days_old=30" in url
    assert "sort_by=date" in url


def test_adzuna_reports_more_pages_when_the_total_exceeds_this_one():
    url_key = None
    http = FakeHTTPClient()
    src = AdzunaSource(app_id="id", app_key="key", country="in",
                       results_per_page=2, page=1, http=http)
    url_key = src._url({"role": "data engineer", "location": "Hyderabad"})
    http._routes[url_key] = {
        "count": 50,
        "results": [
            {"id": 1, "title": "Data Engineer", "redirect_url": "https://x/1",
             "company": {"display_name": "Acme"},
             "location": {"display_name": "Hyderabad, India"},
             "description": "Short snippet.", "created": "2026-07-01T00:00:00Z"},
            {"id": 2, "title": "Data Engineer II", "redirect_url": "https://x/2",
             "company": {"display_name": "Globex"},
             "location": {"display_name": "Bengaluru, India"},
             "description": "Short snippet.", "created": "2026-07-01T00:00:00Z"},
        ],
    }
    postings = src.search({"role": "data engineer", "location": "Hyderabad"})
    assert len(postings) == 2
    assert src.has_next is True
