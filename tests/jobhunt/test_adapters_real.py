"""Offline tests for the real Greenhouse / Lever / Ashby adapters.

Each adapter is constructed with a ``FakeHTTPClient`` that returns
recorded JSON. This exercises URL construction, JD parsing, and the
local query filter without hitting the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.adapters import AshbySource, GreenhouseSource, LeverSource
from jobhunt.adapters.base import SourceUnavailable
from jobhunt.adapters.greenhouse import html_to_text
from jobhunt.http import FakeHTTPClient, HTTPClientError

FIX = Path(__file__).parent.parent.parent / "jobhunt" / "fixtures" / "ats"


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ----- shared --------------------------------------------------------------

def test_html_to_text_collapses_whitespace_and_strips_tags():
    out = html_to_text("<p>Hello   <b>world</b></p><ul><li>one</li><li>two</li></ul>")
    assert "Hello" in out and "world" in out
    assert "<" not in out and ">" not in out
    assert "one" in out and "two" in out


# ----- Greenhouse ----------------------------------------------------------

def test_greenhouse_lists_remote_role_and_strips_html():
    http = FakeHTTPClient({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true":
            _load("greenhouse_acme.json")
    })
    src = GreenhouseSource(board_tokens=["acme"], http=http)
    out = src.search({
        "role": "backend engineer",
        "location": "",
        "remote_ok": True,
        "exclude_companies": [],
    })
    assert any(p.title == "Senior Backend Engineer" and p.remote for p in out)
    # JD is plain text, not HTML.
    backend = next(p for p in out if "Senior Backend" in p.title)
    assert "<p>" not in backend.jd_text and "Kubernetes" in backend.jd_text
    # Frontend role is dropped by the role filter.
    assert all("Frontend" not in p.title for p in out)


def test_greenhouse_unavailable_surfaces_as_SourceUnavailable():
    def boom():
        raise HTTPClientError("503")
    http = FakeHTTPClient({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": boom
    })
    src = GreenhouseSource(board_tokens=["acme"], http=http)
    with pytest.raises(SourceUnavailable):
        src.search({"role": "engineer"})


def test_greenhouse_fans_out_across_multiple_boards():
    http = FakeHTTPClient({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true":
            _load("greenhouse_acme.json"),
        "https://boards-api.greenhouse.io/v1/boards/other/jobs?content=true":
            {"jobs": []},
    })
    src = GreenhouseSource(board_tokens=["acme", "other"], http=http)
    src.search({"role": "engineer"})
    assert sorted(http.calls) == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "https://boards-api.greenhouse.io/v1/boards/other/jobs?content=true",
    ]


# ----- Lever ---------------------------------------------------------------

def test_lever_parses_postings_and_drops_unrelated_roles():
    http = FakeHTTPClient({
        "https://api.lever.co/v0/postings/northwind?mode=json":
            _load("lever_northwind.json")
    })
    src = LeverSource(companies=["northwind"], http=http)
    out = src.search({
        "role": "backend engineer",
        "location": "",
        "remote_ok": True,
        "exclude_companies": [],
    })
    titles = [p.title for p in out]
    assert "Senior Backend Engineer" in titles
    assert "Sales Director, Enterprise" not in titles
    # Lever provides createdAt in milliseconds; ensure it converted to seconds.
    senior = next(p for p in out if p.title == "Senior Backend Engineer")
    assert senior.posted_at and 1_700_000_000 < senior.posted_at < 1_800_000_000


# ----- Ashby ---------------------------------------------------------------

def test_ashby_extracts_salary_band_from_compensation_tier():
    http = FakeHTTPClient({
        "https://api.ashbyhq.com/posting-api/job-board/contoso":
            _load("ashby_contoso.json")
    })
    src = AshbySource(companies=["contoso"], http=http)
    out = src.search({
        "role": "software engineer",
        "location": "",
        "remote_ok": True,
        "exclude_companies": [],
    })
    senior = next(p for p in out if "Agent Platform" in p.title)
    assert senior.salary_min == 180_000 and senior.salary_max == 220_000
    assert senior.remote is True


def test_ashby_location_filter_falls_back_to_remote_when_remote_ok():
    http = FakeHTTPClient({
        "https://api.ashbyhq.com/posting-api/job-board/contoso":
            _load("ashby_contoso.json")
    })
    src = AshbySource(companies=["contoso"], http=http)
    # Looking for "Berlin" — no fixture posting has it. Remote-flagged
    # role should still come through because remote_ok=True.
    out = src.search({
        "role": "software engineer",
        "location": "Berlin",
        "remote_ok": True,
        "exclude_companies": [],
    })
    assert any(p.remote for p in out)
    # NYC EM role (not remote) must be filtered out.
    assert all(not (p.title.startswith("Engineering Manager") and not p.remote) for p in out)
