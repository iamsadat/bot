"""Offline tests for the keyless job-source adapters (Arbeitnow/RemoteOK).
Uses FakeHTTPClient with inline fixtures — no network."""

from __future__ import annotations

import pytest

from jobhunt.adapters import ArbeitnowSource, RemoteOKSource
from jobhunt.adapters.base import SourceUnavailable
from jobhunt.http import FakeHTTPClient, HTTPClientError

_Q = {"role": "backend engineer", "location": "", "remote_ok": True, "exclude_companies": []}


# ----- Arbeitnow -------------------------------------------------------------

def test_arbeitnow_parses_row_and_has_next():
    url = "https://www.arbeitnow.com/api/job-board-api?page=1"
    http = FakeHTTPClient({url: {
        "data": [{
            "slug": "backend-engineer-acme",
            "company_name": "Acme",
            "title": "Backend Engineer",
            "description": "<p>Python and Kubernetes</p>",
            "remote": "True",
            "url": "https://arbeitnow.com/j/backend-engineer-acme",
            "tags": ["python"],
            "job_types": ["full_time"],
            "location": "Berlin",
            "created_at": "1785321027",
        }],
        "links": {"first": url, "last": None, "prev": None, "next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
        "meta": {},
    }})
    src = ArbeitnowSource(http=http)
    out = src.search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.job_id == "arbeitnow:backend-engineer-acme"
    assert p.source == "arbeitnow" and p.source_id == "backend-engineer-acme"
    assert p.company == "Acme" and p.title == "Backend Engineer"
    assert "<" not in p.jd_text and "Kubernetes" in p.jd_text
    assert p.remote is True
    assert p.posted_at == 1785321027.0
    assert src.has_next is True


def test_arbeitnow_remote_false_string_and_no_next():
    url = "https://www.arbeitnow.com/api/job-board-api?page=1"
    http = FakeHTTPClient({url: {
        "data": [{
            "slug": "onsite-role",
            "company_name": "Acme",
            "title": "Backend Engineer",
            "description": "",
            "remote": "False",
            "url": "https://arbeitnow.com/j/onsite-role",
            "location": "Berlin",
            "created_at": "not-a-number",
        }],
        "links": {"first": url, "last": url, "prev": None, "next": None},
        "meta": {},
    }})
    src = ArbeitnowSource(http=http)
    out = src.search(_Q)
    assert len(out) == 1
    assert out[0].remote is False
    assert src.has_next is False


def test_arbeitnow_unavailable_raises():
    def boom():
        raise HTTPClientError("503")
    url = "https://www.arbeitnow.com/api/job-board-api?page=1"
    http = FakeHTTPClient({url: boom})
    with pytest.raises(SourceUnavailable):
        ArbeitnowSource(http=http).search(_Q)


# ----- RemoteOK ----------------------------------------------------------

def test_remoteok_skips_legal_notice_and_parses_row():
    http = FakeHTTPClient({"https://remoteok.com/api": [
        {"last_updated": "1785321027", "legal": "https://remoteok.com/legal"},
        {
            "id": "12345",
            "slug": "backend-engineer-globex",
            "company": "Globex",
            "position": "Backend Engineer",
            "location": "Worldwide",
            "url": "https://remoteok.com/remote-jobs/12345",
            "apply_url": "https://globex.example/apply/12345",
            "date": "2026-01-01T00:00:00+00:00",
            "tags": ["python", "backend"],
            "description": "<p>Build <b>Kubernetes</b> services</p>",
            "salary_min": 120000,
            "salary_max": 160000,
        },
    ]})
    out = RemoteOKSource(http=http).search(_Q)
    assert len(out) == 1
    p = out[0]
    assert p.job_id == "remoteok:12345"
    assert p.source == "remoteok" and p.source_id == "12345"
    assert p.title == "Backend Engineer" and p.company == "Globex"
    assert p.url == "https://remoteok.com/remote-jobs/12345"
    assert "<" not in p.jd_text and "Kubernetes" in p.jd_text
    assert p.remote is True
    assert p.salary_min == 120000 and p.salary_max == 160000
    assert p.raw["attribution"] == "Remote OK"


def test_remoteok_skips_rows_missing_position_or_id():
    http = FakeHTTPClient({"https://remoteok.com/api": [
        {"last_updated": "x", "legal": "y"},
        {"id": "1"},  # missing position
        {"position": "Backend Engineer"},  # missing id
    ]})
    out = RemoteOKSource(http=http).search(_Q)
    assert out == []


def test_remoteok_prefers_url_over_apply_url():
    http = FakeHTTPClient({"https://remoteok.com/api": [
        {"last_updated": "x", "legal": "y"},
        {
            "id": "9", "position": "Backend Engineer", "company": "Globex",
            "apply_url": "https://globex.example/apply/9",
            "date": "2026-01-01T00:00:00+00:00",
        },
    ]})
    out = RemoteOKSource(http=http).search(_Q)
    assert out[0].url == "https://globex.example/apply/9"


def test_remoteok_unavailable_raises():
    def boom():
        raise HTTPClientError("503")
    http = FakeHTTPClient({"https://remoteok.com/api": boom})
    with pytest.raises(SourceUnavailable):
        RemoteOKSource(http=http).search(_Q)
