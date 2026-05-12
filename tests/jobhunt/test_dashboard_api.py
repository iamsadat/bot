"""HTTP tests for the FastAPI dashboard (Phase 2 approval flow).

Drives the routes via TestClient — no live server, no network.
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
    r = client.post(f"/api/approve/{req.request_id}",
                    params={"decision": "nope"})
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
    r = client.post(f"/api/approve/{req.request_id}",
                    params={"decision": "approve"})
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
