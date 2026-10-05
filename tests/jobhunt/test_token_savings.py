"""LLM spend: cached edits, a light model, a breaker, and work only where it pays."""

import json
import threading

from jobhunt.dashboard import server
from jobhunt.dashboard.server import (
    DashboardState, _persist_tailored_docs, _start_polish, _sweep_budget, _write_cover_letter,
)
from jobhunt.agents.resume import ResumeArchitectAgent, ResumeInputs
from jobhunt.llm.cache import TextCache, usage
from jobhunt.llm.callbacks import focused_jd, resume_callback, role_key
from jobhunt.models import JobPosting, ReasoningTrace, UserProfile
from jobhunt.trace import ThoughtBus, TraceStore

BULLETS = ["Built Spark pipelines on Databricks that processed 150M records.",
           "Migrated 150 SAS files to PySpark while preserving business logic."]


class CountingClient:
    def __init__(self):
        self.calls = []

    def complete(self, system, user, *, max_tokens=512, model=None):
        self.calls.append((system.split("\n")[0], model))
        if "JSON only: an array" in system:
            drafts = json.loads(user[user.index("["):])
            return json.dumps([d.replace("Built", "Engineered") for d in drafts])
        return ""


def _profile():
    return UserProfile(
        user_id="u", name="Ada Lovelace", email="ada@example.com",
        target_roles=["Data Engineer"], locations=["Hyderabad"],
        skills=["python", "spark", "databricks"],
        experiences=[{"title": "Data Engineer", "company": "Acme", "start": "2025",
                      "end": "Present", "bullets": BULLETS}],
    )


def _posting(n, title="Senior Data Engineer", score=0.9):
    return JobPosting(job_id=f"j{n}", source="t", source_id=f"j{n}", url="https://x",
                      title=title, company=f"Co{n}", location="Hyderabad",
                      jd_text="Python, Spark and Databricks.", relevance_score=score)


def test_role_key_ignores_level_and_numbering():
    assert role_key("07.Data Engineer II") == role_key("Senior Data Engineer") == "data engineer"


def test_same_entry_same_role_is_edited_once():
    client = CountingClient()
    cb = resume_callback(client, cache=TextCache(None))
    agent = ResumeArchitectAgent(TraceStore(), ThoughtBus(), llm=cb, ai_cover_letter=False)
    postings = [_posting(1), _posting(2, "Data Engineer"), _posting(3, "Lead Data Engineer")]
    docs = agent.act(ResumeInputs(profile=_profile(), postings=postings),
                     ReasoningTrace.new("resume", "t"))
    bullet_calls = [c for c in client.calls if c[0].startswith("You lightly edit the bullets")]
    assert len(bullet_calls) == 1  # three postings, one role → one edit
    assert all("Engineered Spark" in d.resume_text for d in docs)
    assert usage.snapshot()["today"]["cached"] >= 2
    # No cover letter calls during a bulk rewrite.
    assert not any("cover letters" in c[0] for c in client.calls)


def test_light_model_used_for_small_edits(monkeypatch):
    from jobhunt.llm import claude_code_client as ccc

    monkeypatch.setattr(ccc.shutil, "which", lambda _: "/bin/claude")
    client = ccc.ClaudeCodeLLMClient()
    seen = []
    monkeypatch.setattr(client, "complete",
                        lambda system, user, *, max_tokens=512, model=None:
                        seen.append(model) or "")
    cb = resume_callback(client, cache=TextCache(None))
    cb("rewrite_bullets", {"bullets": BULLETS, "title": "Data Engineer"})
    cb("cover_letter", {"profile": {}, "posting_title": "x", "posting_company": "y"})
    assert seen == ["haiku", None]


def test_breaker_stops_after_two_failures():
    calls = []

    def failing(action, payload):
        calls.append(action)
        raise RuntimeError("claude hung")

    budgeted = _sweep_budget(failing)
    for _ in range(2):
        try:
            budgeted("summary", {})
        except RuntimeError:
            pass
    assert budgeted("summary", {}) == "" and len(calls) == 2
    assert budgeted.produced == 0


def test_focused_jd_skips_company_pitch():
    jd = "We are a great company. " * 200 + "Requirements: Python, Spark, Airflow. " * 30
    out = focused_jd(jd, 500)
    assert out.startswith("Requirements") and len(out) <= 500
    assert focused_jd("short", 500) == "short"


def _state(postings, monkeypatch, client):
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: client)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.user_profile = _profile()
    for p in postings:
        state.jobs.append({"job_id": p.job_id, "title": p.title, "company": p.company,
                           "url": p.url, "status": "Saved", "relevance_score": p.relevance_score})
    agent = ResumeArchitectAgent(state.trace_store, state.bus)
    _persist_tailored_docs(state, agent.act(ResumeInputs(profile=state.user_profile,
                                                         postings=postings),
                                            ReasoningTrace.new("resume", "t")))
    return state


def _join():
    for t in threading.enumerate():
        if t.name == "resume-polish":
            t.join(10)


def test_only_good_matches_rewritten_and_only_marked_when_ai_wrote(monkeypatch):
    postings = [_posting(1, score=0.9), _posting(2, score=0.5)]
    state = _state(postings, monkeypatch, CountingClient())
    assert _start_polish(state, postings) == 1  # 50% match keeps the template
    _join()
    assert state.documents["j1"]["ai_status"] == "done"
    assert "ai_status" not in state.documents["j2"]

    # An LLM that writes nothing leaves the résumé unmarked, not "done".
    class Silent:
        def complete(self, *a, **k):
            return ""
    from jobhunt.llm import cache
    monkeypatch.setattr(cache, "_cache", TextCache(None))  # no edit to reuse
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: Silent())
    assert _start_polish(state, [postings[1]], force=True) == 1
    _join()
    assert "ai_status" not in state.documents["j2"]


def test_cover_letter_written_on_demand(monkeypatch):
    letter = ("Dear Hiring Team at Co1,\n\n" + "I write Python and Spark jobs on "
              "Databricks every day. " * 12 + "\n\nRegards,\nAda Lovelace")

    class Writer:
        def complete(self, system, user, **_):
            return letter if "cover letters" in system else ""

    state = _state([_posting(1)], monkeypatch, Writer())
    assert state.documents["j1"]["jd_text"]
    assert _write_cover_letter(state, "j1") is True
    assert state.documents["j1"]["cover_ai"] is True
    assert _write_cover_letter(state, "j1") is False  # once
