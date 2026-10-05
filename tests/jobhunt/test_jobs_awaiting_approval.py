"""/api/jobs flags jobs whose approval request is waiting on the human."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _flags(client: TestClient) -> dict[str, bool]:
    return {j["job_id"]: j["awaiting_approval"]
            for j in client.get("/api/jobs").json()["jobs"]}


def test_awaiting_approval_tracks_pending_requests():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.jobs = [
        {"job_id": "a", "title": "DE", "company": "Acme", "status": "Saved"},
        {"job_id": "r", "title": "DE", "company": "Rho", "status": "Saved"},
        {"job_id": "n", "title": "DE", "company": "Nil", "status": "Saved"},
    ]
    q = state.approval_queue
    ra = q.submit(job_id="a", document_id="da", company="Acme", title="DE")
    rr = q.submit(job_id="r", document_id="dr", company="Rho", title="DE")
    client = TestClient(create_app(state))

    assert _flags(client) == {"a": True, "r": True, "n": False}

    q.approve(ra.request_id)
    q.reject(rr.request_id)
    assert _flags(client) == {"a": False, "r": False, "n": False}
    # The stored job dicts aren't mutated by the read.
    assert "awaiting_approval" not in state.jobs[0]
