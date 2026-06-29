"""Offline tests for the new job-source adapters (Recruitee/Workable/Personio/
Adzuna/USAJobs). Each uses FakeHTTPClient with inline fixtures — no network."""

from __future__ import annotations

import pytest

from jobhunt.adapters import (
    AdzunaSource, PersonioSource, RecruiteeSource, USAJobsSource, WorkableSource,
)
from jobhunt.adapters.base import SourceUnavailable
from jobhunt.http import FakeHTTPClient, HTTPClientError

_Q = {"role": "backend engineer", "location": "", "remote_ok": True, "exclude_companies": []}


# ----- Recruitee -----------------------------------------------------------

def test_recruitee_parses_and_strips_html():
    http = FakeHTTPClient({
        "https://acme.recruitee.com/api/offers/": {"offers": [{
            "id": 1, "title": "Senior Backend Engineer", "location": "Remote",
            "careers_url": "https://acme.recruitee.com/o/1",
            "description": "<p>Build <b>Kubernetes</b> services</p>",
            "published_at": "2026-01-01T00:00:00Z",
        }]},
    })
    out = RecruiteeSource(companies=["acme"], http=http).search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.title == "Senior Backend Engineer" and p.remote
    assert "<" not in p.jd_text and "Kubernetes" in p.jd_text
    assert p.source == "recruitee" and p.url.endswith("/o/1")


def test_recruitee_unavailable_raises():
    def boom():
        raise HTTPClientError("503")
    http = FakeHTTPClient({"https://x.recruitee.com/api/offers/": boom})
    with pytest.raises(SourceUnavailable):
        RecruiteeSource(companies=["x"], http=http).search(_Q)


# ----- Workable ------------------------------------------------------------

def test_workable_parses_location_and_remote():
    url = "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    http = FakeHTTPClient({url: {"name": "Acme", "jobs": [{
        "title": "Backend Engineer", "shortcode": "ABC123",
        "url": "https://acme.workable.com/j/ABC123",
        "location": {"city": "Berlin", "country": "Germany"},
        "telecommuting": True,
        "description": "<p>Python and Kubernetes</p>",
        "published_on": "2026-02-02",
    }]}})
    out = WorkableSource(accounts=["acme"], http=http).search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.company == "Acme" and "Berlin" in p.location and p.remote
    assert p.source_id == "ABC123"


# ----- Personio (XML) ------------------------------------------------------

def test_personio_parses_xml_feed():
    xml = """<?xml version='1.0'?><workzag-jobs>
      <position><id>42</id><office>Remote</office><department>Eng</department>
      <name>Backend Engineer</name>
      <jobDescriptions>
        <jobDescription><name>Role</name><value>&lt;p&gt;Kubernetes&lt;/p&gt;</value></jobDescription>
      </jobDescriptions>
      <createdAt>2026-03-03</createdAt></position>
    </workzag-jobs>"""
    http = FakeHTTPClient(text_routes={"https://acme.jobs.personio.com/xml": xml})
    out = PersonioSource(companies=["acme"], http=http).search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.title == "Backend Engineer" and p.remote
    assert "Kubernetes" in p.jd_text and "<" not in p.jd_text
    assert p.url == "https://acme.jobs.personio.com/job/42"


def test_personio_bad_xml_raises():
    http = FakeHTTPClient(text_routes={"https://x.jobs.personio.com/xml": "<not xml"})
    with pytest.raises(SourceUnavailable):
        PersonioSource(companies=["x"], http=http).search(_Q)


# ----- Adzuna --------------------------------------------------------------

def test_adzuna_builds_search_url_and_parses_salary():
    src = AdzunaSource(app_id="id1", app_key="key1", country="us")
    url = src._url(_Q)
    assert "app_id=id1" in url and "what=backend+engineer" in url
    http = FakeHTTPClient({url: {"results": [{
        "id": "9", "title": "Backend Engineer",
        "redirect_url": "https://adzuna/x", "description": "Python role",
        "location": {"display_name": "Remote, US"},
        "company": {"display_name": "Globex"},
        "salary_min": 150000.0, "salary_max": 200000.0,
        "created": "2026-04-04T00:00:00Z",
    }]}})
    src._http = http
    out = src.search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.company == "Globex" and p.salary_min == 150000 and p.remote


def test_adzuna_requires_keys():
    with pytest.raises(ValueError):
        AdzunaSource(app_id="", app_key="", country="us")


# ----- USAJobs -------------------------------------------------------------

def test_usajobs_parses_items_and_sends_auth_headers():
    src = USAJobsSource(email="me@x.com", api_key="K")
    url = src._url(_Q)
    http = FakeHTTPClient({url: {"SearchResult": {"SearchResultItems": [{
        "MatchedObjectId": "777",
        "MatchedObjectDescriptor": {
            "PositionTitle": "Backend Engineer",
            "PositionURI": "https://usajobs/777",
            "PositionLocationDisplay": "Remote",
            "OrganizationName": "GSA",
            "UserArea": {"Details": {"JobSummary": "Build services"}},
            "PositionRemuneration": [{"MinimumRange": "120000", "MaximumRange": "160000"}],
            "PublicationStartDate": "2026-05-05",
        },
    }]}}})
    src._http = http
    out = src.search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.company == "GSA" and p.salary_max == 160000 and p.source == "usajobs"
    assert p.url == "https://usajobs/777"


def test_usajobs_requires_credentials():
    with pytest.raises(ValueError):
        USAJobsSource(email="", api_key="")
