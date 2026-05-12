"""HTTP tests for the FastAPI dashboard.

Covers the approval flow (Phase 2) and the new onboarding + hunt endpoints
(Phase 3).  All tests run offline via TestClient — no live server, no network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _client():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return state, TestClient(create_app(state))


def _profile_payload(**overrides):
    base = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "target_roles": ["backend engineer"],
        "locations": ["Remote"],
    }
    base.update(overrides)
    return base


# ── /api/status ──────────────────────────────────────────────────────────────

def test_status_initial():
    _, client = _client()
    r = client.get("/api/status")
    assert r.status_code == 200
    d = r.json()
    assert d["hunt_status"] == "idle"
    assert d["has_profile"] is False
    assert d["jobs_count"] == 0


# ── /api/onboarding/profile ───────────────────────────────────────────────────

def test_save_profile_ok():
    state, client = _client()
    r = client.post("/api/onboarding/profile", json=_profile_payload())
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert state.user_profile is not None
    assert state.user_profile.name == "Ada Lovelace"
    assert state.user_profile.email == "ada@example.com"


def test_save_profile_missing_name_422():
    _, client = _client()
    r = client.post("/api/onboarding/profile", json=_profile_payload(name=""))
    assert r.status_code == 422


def test_save_profile_bad_email_422():
    _, client = _client()
    r = client.post("/api/onboarding/profile", json=_profile_payload(email="notanemail"))
    assert r.status_code == 422


def test_save_profile_empty_roles_422():
    _, client = _client()
    r = client.post("/api/onboarding/profile", json=_profile_payload(target_roles=[]))
    assert r.status_code == 422


def test_save_profile_with_salary_and_skills():
    state, client = _client()
    r = client.post("/api/onboarding/profile", json=_profile_payload(
        min_salary=150_000,
        remote_ok=True,
        skills=["python", "redis"],
        weekly_target=5,
    ))
    assert r.status_code == 200
    assert state.user_profile.min_salary == 150_000
    assert "python" in state.user_profile.skills
    assert state.user_profile.weekly_target == 5


# ── /api/onboarding/resume ────────────────────────────────────────────────────

def test_parse_resume_returns_skills():
    _, client = _client()
    r = client.post("/api/onboarding/resume", json={
        "text": "Senior backend engineer with Python, Redis, Kubernetes, FastAPI"
    })
    assert r.status_code == 200
    data = r.json()
    assert "python" in data["skills"]
    assert "redis" in data["skills"]


def test_parse_resume_empty_text_422():
    _, client = _client()
    r = client.post("/api/onboarding/resume", json={"text": "  "})
    assert r.status_code == 422


def test_parse_resume_merges_into_profile():
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload(skills=["go"]))
    client.post("/api/onboarding/resume", json={"text": "Python developer using redis"})
    assert "go" in state.user_profile.skills
    assert "python" in state.user_profile.skills


# ── /api/hunt/start ───────────────────────────────────────────────────────────

def test_start_hunt_without_profile_400():
    _, client = _client()
    r = client.post("/api/hunt/start", json={})
    assert r.status_code == 400


def test_start_hunt_ok(monkeypatch):
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())

    # Patch asyncio.create_task to avoid actually running the orchestrator
    import asyncio
    created = []
    original_create_task = asyncio.create_task

    def mock_create_task(coro, **kw):
        # Close the coroutine to prevent ResourceWarning
        coro.close()
        created.append(True)
        return None

    monkeypatch.setattr(asyncio, "create_task", mock_create_task)
    r = client.post("/api/hunt/start", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert len(created) == 1


def test_start_hunt_twice_409(monkeypatch):
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())

    import asyncio
    monkeypatch.setattr(asyncio, "create_task", lambda c, **kw: c.close() or None)

    client.post("/api/hunt/start", json={})
    # Manually set running so second call hits the guard
    state.hunt_status = "running"
    r = client.post("/api/hunt/start", json={})
    assert r.status_code == 409


# ── /api/approvals ────────────────────────────────────────────────────────────

def test_list_approvals_empty():
    _, client = _client()
    r = client.get("/api/approvals")
    assert r.status_code == 200
    assert r.json() == {"approvals": []}


def test_approve_by_request_id():
    state, client = _client()
    req = state.approval_queue.submit(
        job_id="j-1", document_id="d-1",
        company="Acme", title="Backend",
    )
    r = client.post(
        f"/api/approve/{req.request_id}",
        params={"decision": "approve", "reviewer": "ada"},
    )
    assert r.status_code == 200
    assert r.json()["request"]["state"] == "approved"
    assert r.json()["request"]["reviewer"] == "ada"


def test_approve_falls_back_to_job_id():
    state, client = _client()
    state.approval_queue.submit(
        job_id="j-9", document_id="d-9",
        company="Globex", title="Staff",
    )
    r = client.post("/api/approve/j-9", params={"decision": "reject"})
    assert r.status_code == 200
    assert r.json()["request"]["state"] == "rejected"


def test_approve_bad_decision_400():
    state, client = _client()
    req = state.approval_queue.submit(
        job_id="j-1", document_id="d-1", company="X", title="Y",
    )
    r = client.post(f"/api/approve/{req.request_id}", params={"decision": "nope"})
    assert r.status_code == 400


def test_approve_unknown_request_404():
    _, client = _client()
    r = client.post("/api/approve/unknown-id", params={"decision": "approve"})
    assert r.status_code == 404


def test_invalid_transition_409():
    state, client = _client()
    req = state.approval_queue.submit(
        job_id="j-1", document_id="d-1", company="X", title="Y",
    )
    state.approval_queue.reject(req.request_id)
    r = client.post(f"/api/approve/{req.request_id}", params={"decision": "approve"})
    assert r.status_code == 409


def test_list_approvals_filter():
    state, client = _client()
    a = state.approval_queue.submit(
        job_id="j-1", document_id="d-1", company="A", title="T",
    )
    b = state.approval_queue.submit(
        job_id="j-2", document_id="d-2", company="B", title="T",
    )
    state.approval_queue.approve(a.request_id)

    r = client.get("/api/approvals?state_filter=pending")
    rids = [x["request_id"] for x in r.json()["approvals"]]
    assert rids == [b.request_id]

    r = client.get("/api/approvals?state_filter=approved")
    rids = [x["request_id"] for x in r.json()["approvals"]]
    assert rids == [a.request_id]


def test_approve_publishes_to_thought_bus():
    state, client = _client()
    req = state.approval_queue.submit(
        job_id="j-1", document_id="d-1",
        company="Acme", title="Backend",
    )
    client.post(f"/api/approve/{req.request_id}",
                params={"decision": "approve", "reviewer": "ada"})
    history = state.bus.history()
    assert any(h.get("agent") == "approval" for h in history)


# ── /api/plan and /api/jobs ───────────────────────────────────────────────────

def test_get_plan_empty():
    _, client = _client()
    r = client.get("/api/plan")
    assert r.status_code == 200
    assert r.json() == {"plan": None}


def test_get_jobs_empty():
    _, client = _client()
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


def test_get_jobs_populated():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "title": "Backend", "company": "Acme",
                   "location": "Remote", "status": "Saved", "relevance_score": 0.9}]
    r = client.get("/api/jobs")
    assert len(r.json()["jobs"]) == 1
    assert r.json()["jobs"][0]["title"] == "Backend"
