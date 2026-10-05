"""Sweeps tailor instantly; the LLM rewrite happens afterwards, in the background."""

from jobhunt.dashboard import server
from jobhunt.dashboard.server import DashboardState, _persist_tailored_docs, _start_polish
from jobhunt.agents.resume import ResumeArchitectAgent, ResumeInputs
from jobhunt.models import JobPosting, ReasoningTrace, UserProfile
from jobhunt.trace import ThoughtBus, TraceStore

SUMMARY = ("Data Engineer building batch and streaming pipelines with Python and "
           "Spark. Built pipelines on Databricks that processed 150M records for "
           "downstream analytics teams.")


class FakeClient:
    def complete(self, system, user, **_):
        return SUMMARY if "summary" in system.lower() else ""


def _profile():
    return UserProfile(
        user_id="u", name="Ada Lovelace", email="ada@example.com",
        target_roles=["Data Engineer"], locations=["Hyderabad"],
        skills=["python", "spark", "databricks"],
        experiences=[{"title": "Data Engineer", "company": "Acme", "start": "2025",
                      "end": "Present",
                      "bullets": ["Built pipelines on Databricks that processed 150M records."]}],
    )


def _posting(n):
    return JobPosting(job_id=f"j{n}", source="t", source_id=f"j{n}", url="https://x",
                      title="Data Engineer", company=f"Co{n}", location="Hyderabad",
                      jd_text="Python, Spark and Databricks.", relevance_score=0.9)


def _state_with_docs(postings):
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.user_profile = _profile()
    agent = ResumeArchitectAgent(state.trace_store, state.bus)  # template only
    docs = agent.act(ResumeInputs(profile=state.user_profile, postings=postings),
                     ReasoningTrace.new("resume", "t"))
    _persist_tailored_docs(state, docs)
    return state


def test_rewrites_in_background_but_never_an_approved_resume(monkeypatch):
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: FakeClient())
    postings = [_posting(1), _posting(2)]
    state = _state_with_docs(postings)
    template = state.documents["j1"]["resume_text"]
    assert SUMMARY not in template

    # The owner approves j2 before Claude gets to it.
    req = state.approval_queue.by_job("j2")[0]
    state.approval_queue.approve(req.request_id)

    with server._POLISH_LOCK:  # hold the worker until both are queued
        assert _start_polish(state, postings) == 2
        assert state.documents["j1"]["ai_status"] == "pending"
    for t in __import__("threading").enumerate():
        if t.name == "resume-polish":
            t.join(10)

    assert state.documents["j1"]["ai_status"] == "done"
    assert SUMMARY in state.documents["j1"]["resume_text"]
    assert "ai_status" not in state.documents["j2"]
    assert SUMMARY not in state.documents["j2"]["resume_text"]


def test_no_llm_means_no_background_work(monkeypatch):
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: None)
    state = _state_with_docs([_posting(1)])
    assert _start_polish(state, [_posting(1)]) == 0
    assert "ai_status" not in state.documents["j1"]
