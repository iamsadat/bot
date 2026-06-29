"""Tests for recruiter-contact enrichment + evidence-bound outreach drafting."""

from __future__ import annotations

import pytest

from jobhunt.http import FakeHTTPClient
from jobhunt.integrations.enrichment import (
    Contact, EnrichmentError, HunterContactFinder, build_contact_finder_from_env,
    domain_from_url, draft_outreach,
)
from jobhunt.models import UserProfile

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def test_domain_from_url_strips_ats_hosts():
    assert domain_from_url("https://boards.greenhouse.io/acme/jobs/1") == ""
    assert domain_from_url("https://www.acme.com/careers/1") == "acme.com"


def test_hunter_finder_parses_contacts():
    finder = HunterContactFinder("KEY", FakeHTTPClient())
    url = finder._BASE.format(qs=__import__("urllib.parse", fromlist=["urlencode"]).urlencode([
        ("domain", "acme.com"), ("api_key", "KEY"),
        ("limit", "10"), ("department", "hr,executive,management")]))
    finder._http = FakeHTTPClient({url: {"data": {"emails": [
        {"value": "jane@acme.com", "first_name": "Jane", "last_name": "Doe",
         "position": "Technical Recruiter"},
        {"value": "", "first_name": "No"},  # dropped (no email)
    ]}}})
    contacts = finder.find("Acme", "acme.com")
    assert len(contacts) == 1
    assert contacts[0].email == "jane@acme.com" and contacts[0].name == "Jane Doe"


def test_hunter_requires_key():
    with pytest.raises(EnrichmentError):
        HunterContactFinder("", FakeHTTPClient())


def test_draft_outreach_is_evidence_bound():
    profile = UserProfile(user_id="u", name="Ada", email="ada@x.com",
                          target_roles=[], locations=[], skills=["python"])
    job = {"company": "Acme", "title": "Backend Engineer"}
    doc = {"matched_keywords": ["python", "kubernetes"]}
    contact = Contact(name="Jane Doe", email="jane@acme.com", title="Recruiter")
    d = draft_outreach(profile, job, doc, contact)
    assert d["to"] == "jane@acme.com"
    assert "python" in d["body"] and "résumé is attached" in d["body"]
    assert "Hi Jane" in d["body"]  # first name


def test_build_finder_env(monkeypatch):
    monkeypatch.delenv("JOBHUNT_HUNTER_API_KEY", raising=False)
    assert build_contact_finder_from_env() is None
    monkeypatch.setenv("JOBHUNT_HUNTER_API_KEY", "K")
    f = build_contact_finder_from_env()
    assert f is not None and f.name == "hunter"


# ----- endpoints -----------------------------------------------------------

def _client():
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    st.user_profile = UserProfile(user_id="u", name="Ada", email="ada@x.com",
                                  target_roles=[], locations=[], skills=["python"])
    st.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                "url": "https://www.acme.com/careers/1", "status": "Applied", "events": []}]
    st.documents["j1"] = {"company": "Acme", "title": "Backend",
                          "matched_keywords": ["python"]}
    return st, TestClient(create_app(st))


def test_outreach_find_gated_off(monkeypatch):
    monkeypatch.delenv("JOBHUNT_HUNTER_API_KEY", raising=False)
    _, c = _client()
    assert c.get("/api/outreach/status").json()["configured"] is False
    assert c.post("/api/outreach/find", json={"job_id": "j1"}).status_code == 400


def test_outreach_draft_works_without_provider():
    _, c = _client()
    r = c.post("/api/outreach/draft",
               json={"job_id": "j1", "contact_name": "Jane Doe",
                     "contact_email": "jane@acme.com"})
    assert r.status_code == 200
    assert "python" in r.json()["body"] and r.json()["to"] == "jane@acme.com"
