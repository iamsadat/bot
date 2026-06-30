"""Tests for the public résumé page + publish flow.

``POST /api/publish`` mints a public handle for a job's tailored draft, and
``GET /p/{handle}`` serves it as an unauthenticated, static HTML page with a
"Built with JobHunt" footer.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.models import JobPosting, UserProfile  # noqa: E402
from jobhunt.resume_template import build_tailored_resume  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _draft_dict() -> dict:
    profile = UserProfile(
        user_id="u1", name="Ada Lovelace", email="ada@x.com", phone="555",
        target_roles=["backend"], locations=["Remote"],
        skills=["python", "kubernetes"],
        experiences=[{
            "title": "Senior Backend Engineer", "company": "Acme",
            "location": "Remote", "start": "Jan 2019", "end": "Present",
            "bullets": ["Built distributed Python services on Kubernetes."],
        }],
    )
    posting = JobPosting(
        job_id="j1", source="gh", source_id="1", url="https://x/y",
        title="Backend Engineer", company="Acme", location="Remote",
        jd_text="Python Kubernetes backend services.",
    )
    draft = build_tailored_resume(profile, posting)
    return dataclasses.asdict(draft)


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_PUBLIC_DB_PATH", str(tmp_path / "public.db"))
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.documents["j1"] = {
        "company": "Acme", "title": "Backend Engineer", "draft": _draft_dict(),
    }
    return state, TestClient(create_app(state))


def test_publish_then_fetch_public_page(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    pub = client.post("/api/publish", json={"job_id": "j1"})
    assert pub.status_code == 200
    body = pub.json()
    assert body["ok"] is True
    handle = body["handle"]
    assert body["url"] == f"/p/{handle}"
    assert handle.startswith("ada-lovelace-")

    page = client.get(f"/p/{handle}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Ada Lovelace" in page.text
    assert "Built with JobHunt" in page.text


def test_unknown_handle_404(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.get("/p/does-not-exist")
    assert r.status_code == 404


def test_publish_unknown_job_id_404(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.post("/api/publish", json={"job_id": "nope"})
    assert r.status_code == 404


def test_publish_job_without_draft_404(tmp_path, monkeypatch):
    state, client = _client(tmp_path, monkeypatch)
    state.documents["j2"] = {"company": "Acme", "title": "X", "draft": None}
    r = client.post("/api/publish", json={"job_id": "j2"})
    assert r.status_code == 404


def test_publish_missing_job_id_422(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.post("/api/publish", json={})
    assert r.status_code == 422
