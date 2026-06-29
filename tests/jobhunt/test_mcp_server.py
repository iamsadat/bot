"""Tests for the MCP server tool logic (offline; SDK wiring is optional)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from jobhunt.dashboard.server import DashboardState  # noqa: E402
from jobhunt.mcp_server import TOOL_SPECS, JobHuntTools  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _state_with_job():
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    st.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                "status": "Saved", "url": "u", "relevance_score": 0.8, "events": []}]
    st.documents["j1"] = {
        "job_id": "j1", "company": "Acme", "title": "Backend",
        "resume_text": "Ada\nada@x.com", "keyword_coverage": 0.9,
        "matched_keywords": ["python"], "missing_keywords": ["rust"]}
    st.approval_queue.submit(job_id="j1", document_id="d", company="Acme", title="Backend")
    return st


def test_specs_cover_every_tool():
    tools = JobHuntTools(_state_with_job())
    names = {s["name"] for s in TOOL_SPECS}
    # Every advertised tool dispatches without an "unknown tool" error.
    for n in names:
        out = tools.call(n, {"job_id": "j1", "role": "backend"})
        assert "unknown tool" not in str(out)


def test_status_and_pipeline():
    t = JobHuntTools(_state_with_job())
    s = t.status()
    assert s["discovered"] == 1 and s["pending_approval"] == 1
    assert "Saved" in t.pipeline()["pipeline"]


def test_get_resume_and_missing():
    t = JobHuntTools(_state_with_job())
    r = t.get_resume("j1")
    assert r["keyword_coverage"] == 0.9 and "python" in r["matched_keywords"]
    assert "error" in t.get_resume("nope")


def test_approve_marks_applied():
    st = _state_with_job()
    t = JobHuntTools(st)
    out = t.approve("j1")
    assert out["ok"] and st.jobs[0]["status"] == "Applied"
    # second approve → no pending request
    assert t.approve("j1")["ok"] is False


def test_unknown_tool():
    assert "unknown tool" in str(JobHuntTools(_state_with_job()).call("nope", {}))


def test_search_jobs_uses_discovery():
    from jobhunt.models import UserProfile
    st = _state_with_job()
    st.user_profile = UserProfile(
        user_id="u", name="Ada", email="ada@x.com",
        target_roles=["backend engineer"], locations=["Remote"],
        skills=["python", "kubernetes"])
    out = JobHuntTools(st).search_jobs("backend engineer", "Remote")
    assert "matches" in out and isinstance(out["matches"], list)


def test_search_jobs_requires_profile():
    out = JobHuntTools(_state_with_job()).search_jobs("backend", "")
    assert "error" in out  # no profile


def test_serve_stdio_requires_mcp(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("mcp"):
            raise ImportError("no mcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from jobhunt.mcp_server import serve_stdio
    with pytest.raises(RuntimeError, match="pip install"):
        serve_stdio()
