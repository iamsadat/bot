"""Derived job details: company tier, LinkedIn contact links, salary choice."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from jobhunt.company_tiers import linkedin_links, tier_for
from jobhunt.integrations.market import SalaryEstimate

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, _cached_estimate, _posting_salary, create_app,
)
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def test_tier_known_normalised_and_unknown():
    assert tier_for("Google") == "Big Tech"
    assert tier_for("Razorpay Software Private Limited") == "Product unicorn"
    assert tier_for("razorpaysoftwareprivatelimited") == "Product unicorn"
    assert tier_for("Infosys Ltd.") == "Services/consulting"
    assert tier_for("CAPCO") == "Services/consulting"
    assert tier_for("Okta, Inc.") == "Mid-size product"
    assert tier_for("Hevo Data Technologies") == "Startup"
    assert tier_for("Some Unknown Co") == ""
    assert tier_for("") == ""


def test_linkedin_links_are_encoded_people_searches():
    links = linkedin_links("Dun & Bradstreet")
    assert [l["label"] for l in links] == [
        "Recruiters at Dun & Bradstreet", "Data engineering managers at Dun & Bradstreet"]
    url = urlparse(links[0]["url"])
    assert url.netloc == "www.linkedin.com" and url.path == "/search/results/people/"
    assert "%26" in url.query and "%5B%22102713980%22%5D" in url.query
    qs = parse_qs(url.query)
    assert qs["keywords"] == ["Dun & Bradstreet recruiter OR talent acquisition"]
    assert qs["geoUrn"] == ['["102713980"]']
    assert linkedin_links("") == []


def test_salary_prefers_posting_band_else_none():
    assert _posting_salary({"salary_min": 100, "salary_max": 200}) == {
        "min": 100, "max": 200, "currency": "", "source": "posting"}
    assert _posting_salary({"salary_min": None, "salary_max": None}) is None
    assert _posting_salary({}) is None


class _CountingClient:
    def __init__(self):
        self.calls = []

    def estimate(self, role, location="", country=None):
        self.calls.append((role, location, country))
        return SalaryEstimate(role, location, "INR", 1, 2, 3, 10)


def test_market_estimate_is_cached_per_role_and_city():
    cache, client = {}, _CountingClient()
    a = _cached_estimate(cache, client, "Senior Data Engineer", "Bangalore, India", "in")
    b = _cached_estimate(cache, client, "Data Engineer II", "bangalore", "in")
    assert a is b and client.calls == [("data engineer", "bangalore", "in")]
    _cached_estimate(cache, client, "Data Engineer", "Hyderabad", "in")
    assert len(client.calls) == 2


def test_api_jobs_carries_tier_salary_and_contacts():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.jobs = [
        {"job_id": "a", "title": "DE", "company": "Capco", "status": "Saved",
         "salary_min": 10, "salary_max": 20},
        {"job_id": "b", "title": "DE", "company": "Nobody", "status": "Saved"},
    ]
    jobs = TestClient(create_app(state)).get("/api/jobs").json()["jobs"]
    assert jobs[0]["tier"] == "Services/consulting"
    assert jobs[0]["salary"]["source"] == "posting"
    assert len(jobs[0]["contacts"]) == 2
    assert jobs[1]["tier"] == "" and jobs[1]["salary"] is None
    assert "tier" not in state.jobs[0]
