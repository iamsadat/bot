"""HTTP tests for the FastAPI dashboard.

Covers the approval flow (Phase 2) and the new onboarding + hunt endpoints
(Phase 3).  All tests run offline via TestClient — no live server, no network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, WorkspaceManager, create_app,
)
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


# ── /api/jobs/{id}/status (manual pipeline transitions) ──────────────────────

def test_job_status_transition_ok():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "title": "X", "company": "Acme",
                   "status": "Saved"}]
    r = client.post("/api/jobs/j-1/status", json={"status": "Applied"})
    assert r.status_code == 200
    assert state.jobs[0]["status"] == "Applied"


def test_job_status_invalid_400():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "status": "Saved"}]
    r = client.post("/api/jobs/j-1/status", json={"status": "Nonsense"})
    assert r.status_code == 400


def test_job_status_unknown_job_404():
    _, client = _client()
    r = client.post("/api/jobs/missing/status", json={"status": "Applied"})
    assert r.status_code == 404


def test_job_status_publishes_to_bus():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "title": "T", "company": "C", "status": "Saved"}]
    client.post("/api/jobs/j-1/status", json={"status": "Interview"})
    history = state.bus.history()
    assert any(h.get("agent") == "tracking" for h in history)


# ── /api/onboarding/ats ──────────────────────────────────────────────────────

def test_save_ats_from_csv_string():
    state, client = _client()
    r = client.post("/api/onboarding/ats", json={
        "greenhouse_tokens": "stripe, airtable",
        "lever_slugs": "netflix",
        "ashby_slugs": "",
    })
    assert r.status_code == 200
    assert state.ats_config["greenhouse_tokens"] == ["stripe", "airtable"]
    assert state.ats_config["lever_slugs"] == ["netflix"]
    assert state.ats_config["ashby_slugs"] == []


def test_save_ats_from_list():
    state, client = _client()
    client.post("/api/onboarding/ats", json={
        "greenhouse_tokens": ["stripe", "airtable"],
        "lever_slugs": [],
        "ashby_slugs": ["vercel"],
    })
    assert state.ats_config["greenhouse_tokens"] == ["stripe", "airtable"]
    assert state.ats_config["ashby_slugs"] == ["vercel"]


def test_status_includes_ats_flag():
    state, client = _client()
    r = client.get("/api/status")
    assert r.json()["ats_configured"] is False
    client.post("/api/onboarding/ats", json={"greenhouse_tokens": ["stripe"]})
    assert client.get("/api/status").json()["ats_configured"] is True


# ── /api/documents/{id} (download) ───────────────────────────────────────────

def _seed_doc(state):
    state.documents["j-1"] = {
        "job_id": "j-1", "company": "Acme", "title": "Backend",
        "url": "https://example.com/jobs/1",
        "resume_text": "Ada Lovelace\nada@example.com\n\nHighlights:\n- python",
        "cover_letter_text": "Dear Acme team,\nI'm applying for Backend.",
        "keyword_coverage": 0.8,
        "matched_keywords": ["python"], "missing_keywords": ["go"],
        "bullets": [{"text": "x", "evidence_id": "skill:0"}],
    }


def test_get_document_ok():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1")
    assert r.status_code == 200
    assert r.json()["document"]["company"] == "Acme"


def test_get_document_404():
    _, client = _client()
    r = client.get("/api/documents/missing")
    assert r.status_code == 404


def test_download_txt():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=txt&kind=resume")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert b"Ada Lovelace" in r.content


def test_download_html():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=html&kind=resume")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert b"Acme" in r.content


def test_download_cover_letter():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=txt&kind=cover")
    assert r.status_code == 200
    assert b"Dear Acme" in r.content


def test_download_unknown_kind_400():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=txt&kind=nope")
    assert r.status_code == 400


def test_download_missing_doc_404():
    _, client = _client()
    r = client.get("/api/documents/missing/download")
    assert r.status_code == 404


def test_download_pdf():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=pdf&kind=resume")
    try:
        import fpdf  # noqa: F401
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content[:4] == b"%PDF"
    except ImportError:
        assert r.status_code == 503
        assert "fpdf2" in r.json()["detail"]


def test_download_docx():
    state, client = _client()
    _seed_doc(state)
    r = client.get("/api/documents/j-1/download?format=docx&kind=resume")
    try:
        import docx  # noqa: F401
        assert r.status_code == 200
        assert r.content[:2] == b"PK"  # docx is a zip
    except ImportError:
        assert r.status_code == 503
        assert "python-docx" in r.json()["detail"]


# ── /api/status extra flags ──────────────────────────────────────────────────

def test_status_exposes_dev_nav_and_llm(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state, dev_nav=True))
    d = client.get("/api/status").json()
    assert d["dev_nav"] is True
    assert d["llm"]["active"] is False and d["llm"]["provider"] is None


def test_status_dev_nav_defaults_false():
    state, client = _client()
    assert client.get("/api/status").json()["dev_nav"] is False


# ── /api/profile (GET/PUT) ───────────────────────────────────────────────────

def test_profile_get_none_before_onboarding():
    _, client = _client()
    assert client.get("/api/profile").json()["profile"] is None


def test_profile_get_after_onboarding():
    _, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    p = client.get("/api/profile").json()["profile"]
    assert p["name"] == "Ada Lovelace"


def test_profile_put_updates_fields():
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    r = client.put("/api/profile", json={
        "skills": ["python", "redis"], "weekly_target": 7,
        "target_roles": "staff engineer, backend engineer",
    })
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["skills"] == ["python", "redis"]
    assert p["weekly_target"] == 7
    assert p["target_roles"] == ["staff engineer", "backend engineer"]
    assert state.user_profile.weekly_target == 7


def test_profile_put_before_onboarding_400():
    _, client = _client()
    r = client.put("/api/profile", json={"name": "X"})
    assert r.status_code == 400


def test_profile_put_rejects_bad_email():
    _, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    r = client.put("/api/profile", json={"email": "not-an-email"})
    assert r.status_code == 422


# ── /api/activity ────────────────────────────────────────────────────────────

def test_activity_feed_returns_bus_history():
    state, client = _client()
    state.bus.publish("discovery", "t", "Found 3 roles")
    state.bus.publish("resume", "t", "Tailored a resume")
    items = client.get("/api/activity").json()["activity"]
    # Newest first.
    assert items[0]["thought"] == "Tailored a resume"
    assert items[1]["thought"] == "Found 3 roles"


# ── /api/hunt/reset ──────────────────────────────────────────────────────────

def test_hunt_reset_clears_state():
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    state.jobs = [{"job_id": "j-1", "status": "Saved"}]
    state.documents["j-1"] = {"company": "X"}

    r = client.post("/api/hunt/reset", json={})
    assert r.status_code == 200
    assert state.user_profile is None
    assert state.jobs == []
    assert state.documents == {}


def test_hunt_reset_blocked_while_running():
    state, client = _client()
    state.hunt_status = "running"
    r = client.post("/api/hunt/reset", json={})
    assert r.status_code == 409


# ── approval auto-advances job to "Applied" ──────────────────────────────────

def test_approve_marks_matching_job_as_applied():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "title": "T", "company": "C", "status": "Saved"}]
    req = state.approval_queue.submit(
        job_id="j-1", document_id="d-1", company="C", title="T",
    )
    client.post(f"/api/approve/{req.request_id}", params={"decision": "approve"})
    assert state.jobs[0]["status"] == "Applied"


# ── multi-tenant workspace isolation (workspace_factory mode) ───────────────

def _workspace_app(tmp_path, cap=200):
    manager = WorkspaceManager(base_dir=tmp_path, cap=cap)
    app = create_app(workspace_factory=manager.get)
    return manager, app


def test_workspace_factory_mode_sets_cookie_on_index():
    with tempfile.TemporaryDirectory() as tmp:
        _, app = _workspace_app(Path(tmp))
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert client.cookies.get("jh_ws") is not None


def test_two_cookie_jars_get_isolated_state():
    with tempfile.TemporaryDirectory() as tmp:
        manager, app = _workspace_app(Path(tmp))
        client_a = TestClient(app)
        client_b = TestClient(app)

        # Each gets its own cookie on first load.
        client_a.get("/")
        client_b.get("/")
        ws_a = client_a.cookies.get("jh_ws")
        ws_b = client_b.cookies.get("jh_ws")
        assert ws_a is not None and ws_b is not None
        assert ws_a != ws_b

        # Each does its own onboarding.
        client_a.post("/api/onboarding/profile", json=_profile_payload(
            name="Ada Lovelace", email="ada@example.com",
        ))
        client_b.post("/api/onboarding/profile", json=_profile_payload(
            name="Grace Hopper", email="grace@example.com",
        ))

        state_a = manager.get(ws_a)
        state_b = manager.get(ws_b)
        assert state_a is not state_b
        assert state_a.user_profile.name == "Ada Lovelace"
        assert state_b.user_profile.name == "Grace Hopper"

        # Jobs and approval queues stay isolated too.
        state_a.jobs = [{"job_id": "j-1", "title": "X", "company": "Acme",
                          "status": "Saved"}]
        assert client_a.get("/api/jobs").json()["jobs"] != []
        assert client_b.get("/api/jobs").json()["jobs"] == []

        state_a.approval_queue.submit(
            job_id="j-1", document_id="d-1", company="Acme", title="X",
        )
        assert len(state_a.approval_queue.all()) == 1
        assert state_b.approval_queue.all() == []


def test_path_traversal_cookie_rejected_and_fresh_workspace_minted():
    with tempfile.TemporaryDirectory() as tmp:
        _, app = _workspace_app(Path(tmp))
        client = TestClient(app)
        r = client.get("/api/status", headers={"Cookie": "jh_ws=../../../etc/passwd"})
        assert r.status_code == 200
        new_ws = r.cookies.get("jh_ws")
        assert new_ws is not None
        assert new_ws != "../../../etc/passwd"
        import re
        assert re.match(r"^[a-f0-9]{32}$", new_ws)

        # No file escaping the workspaces dir was ever touched.
        assert not (Path(tmp) / ".." / "etc" / "passwd").resolve().exists()


def test_workspace_manager_evicts_lru_beyond_cap():
    with tempfile.TemporaryDirectory() as tmp:
        manager = WorkspaceManager(base_dir=Path(tmp), cap=2)
        ws1 = "a" * 32
        ws2 = "b" * 32
        ws3 = "c" * 32
        s1 = manager.get(ws1)
        manager.get(ws2)
        assert len(manager) == 2
        manager.get(ws3)  # evicts ws1 (least-recently-used)
        assert len(manager) == 2
        # ws1 is no longer cached, so .get() constructs a brand-new object.
        s1_again = manager.get(ws1)
        assert s1_again is not s1


# ── access-code gate (off by default, opt-in via `access_code=`) ────────────

def test_access_code_off_by_default_existing_tests_unaffected():
    _, client = _client()
    r = client.get("/api/status")
    assert r.status_code == 200


def test_access_code_missing_returns_401():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 401
    assert r.json()["detail"] == "access code required"


def test_access_code_wrong_returns_401():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    r = client.get("/api/status", headers={"X-Access-Code": "nope"})
    assert r.status_code == 401


def test_access_code_correct_header_passes():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    r = client.get("/api/status", headers={"X-Access-Code": "letmein"})
    assert r.status_code == 200


def test_access_code_correct_query_param_passes():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    r = client.get("/api/status?code=letmein")
    assert r.status_code == 200


def test_access_code_does_not_gate_index_or_static_companions():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/demo").status_code == 200


def test_access_code_gates_websocket():
    from fastapi import WebSocketDisconnect

    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state, access_code="letmein")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/stream"):
            pass


# ── auto-apply on approve ────────────────────────────────────────────────────

def _registry_client(poster):
    """Client whose approve flow uses a FakePoster-backed Greenhouse/Lever registry."""
    from jobhunt.submitters.greenhouse import GreenhouseSubmitter
    from jobhunt.submitters.lever import LeverSubmitter
    from jobhunt.submitters.registry import SubmitterRegistry
    reg = SubmitterRegistry([GreenhouseSubmitter(poster), LeverSubmitter(poster)])
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return state, TestClient(create_app(state, submitter_registry=reg))


def _seed_greenhouse_job(state):
    url = "https://boards.greenhouse.io/acme/jobs/123"
    state.jobs = [{
        "job_id": "123", "company": "Acme", "title": "Backend",
        "url": url, "status": "Saved", "events": [],
    }]
    state.documents["123"] = {
        "job_id": "123", "company": "Acme", "title": "Backend", "url": url,
        "resume_text": "Ada Lovelace\nada@example.com", "cover_letter_text": "Dear Acme",
        "keyword_coverage": 0.8, "matched_keywords": ["python"], "missing_keywords": [],
        "bullets": [],
    }
    state.approval_queue.submit(job_id="123", document_id="d", company="Acme", title="Backend")


def test_approve_auto_submits_when_ats_connected():
    from jobhunt.submitters.base import FakePoster
    poster = FakePoster()
    poster.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 201, {"id": "GH-999"})
    state, client = _registry_client(poster)
    client.post("/api/onboarding/profile", json=_profile_payload(phone="+15551234"))
    _seed_greenhouse_job(state)
    state.ats_config = {"greenhouse_tokens": ["acme"], "lever_slugs": [], "ashby_slugs": []}

    r = client.post("/api/approve/123?decision=approve")
    assert r.status_code == 200
    assert r.json()["submission"] == {"submitted": True, "submission_id": "GH-999"}
    assert len(poster.calls) == 1
    # Applicant payload carried name/email/phone from the profile.
    sent = poster.calls[0]["body"]
    assert b"ada@example.com" in sent and b"+15551234" in sent
    job = state.jobs[0]
    assert job["submitted"] is True and job["submission_id"] == "GH-999"
    assert job["status"] == "Applied"


def test_approve_does_not_submit_for_fixtures_without_ats():
    from jobhunt.submitters.base import FakePoster
    poster = FakePoster()
    state, client = _registry_client(poster)
    client.post("/api/onboarding/profile", json=_profile_payload())
    _seed_greenhouse_job(state)  # greenhouse-looking URL but no ATS connected

    r = client.post("/api/approve/123?decision=approve")
    assert r.json()["submission"] == {"submitted": False, "manual": True}
    assert len(poster.calls) == 0  # never hit the network for fixtures
    assert state.jobs[0]["status"] == "Applied"
    assert not state.jobs[0].get("submitted")


def test_approve_surfaces_submission_failure():
    from jobhunt.submitters.base import FakePoster
    poster = FakePoster()
    poster.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 422, {"error": "bad"})
    state, client = _registry_client(poster)
    client.post("/api/onboarding/profile", json=_profile_payload())
    _seed_greenhouse_job(state)
    state.ats_config = {"greenhouse_tokens": ["acme"], "lever_slugs": [], "ashby_slugs": []}

    r = client.post("/api/approve/123?decision=approve")
    sub = r.json()["submission"]
    assert sub["submitted"] is False and "422" in sub["detail"]
    assert not state.jobs[0].get("submitted")


def test_approve_no_duplicate_submit():
    from jobhunt.submitters.base import FakePoster
    poster = FakePoster()
    poster.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 201, {"id": "GH-1"})
    state, client = _registry_client(poster)
    client.post("/api/onboarding/profile", json=_profile_payload())
    _seed_greenhouse_job(state)
    state.ats_config = {"greenhouse_tokens": ["acme"], "lever_slugs": [], "ashby_slugs": []}

    client.post("/api/approve/123?decision=approve")
    # A second approve of an already-SUBMITTED request is an invalid transition.
    r2 = client.post("/api/approve/123?decision=approve")
    assert r2.status_code in (404, 409)
    assert len(poster.calls) == 1  # not submitted twice


# ── per-application timeline ─────────────────────────────────────────────────

def test_timeline_records_status_changes():
    state, client = _client()
    state.jobs = [{"job_id": "j-1", "company": "X", "title": "Eng", "status": "Saved", "events": []}]
    client.post("/api/jobs/j-1/status", json={"status": "Interview"})
    tl = client.get("/api/jobs/j-1/timeline").json()["timeline"]
    assert tl and tl[-1]["stage"] == "Interview"
    assert "Saved → Interview" in tl[-1]["detail"]


def test_timeline_404_for_unknown_job():
    _, client = _client()
    assert client.get("/api/jobs/nope/timeline").status_code == 404


# ── profile phone ────────────────────────────────────────────────────────────

def test_profile_put_updates_phone():
    _, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    r = client.put("/api/profile", json={"phone": "+1 555 000 1111"})
    assert r.json()["profile"]["phone"] == "+1 555 000 1111"


# ── editable culture keywords + ATS sources ──────────────────────────────────

def test_profile_put_updates_culture_keywords():
    _, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    r = client.put("/api/profile", json={"culture_keywords": ["remote-first", "async"]})
    assert r.json()["profile"]["culture_keywords"] == ["remote-first", "async"]


def test_profile_get_returns_ats_config_for_editing():
    state, client = _client()
    client.post("/api/onboarding/profile", json=_profile_payload())
    client.post("/api/onboarding/ats", json={"greenhouse_tokens": "stripe, airtable"})
    g = client.get("/api/profile").json()
    assert g["ats_config"]["greenhouse_tokens"] == ["stripe", "airtable"]


# ── application tracker: notes / next action ─────────────────────────────────

def test_job_notes_set_and_persist():
    state, client = _client()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Eng", "status": "Applied"}]
    r = client.post("/api/jobs/j1/notes", json={"notes": "called", "next_action": "follow up Fri"})
    assert r.status_code == 200
    assert state.jobs[0]["notes"] == "called"
    assert state.jobs[0]["next_action"] == "follow up Fri"


def test_job_notes_404_for_unknown_job():
    _, client = _client()
    assert client.post("/api/jobs/nope/notes", json={"notes": "x"}).status_code == 404
