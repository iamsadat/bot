"""The in-app assistant: JSON action protocol, validation and confirmation gates.

Fully offline: a FakeLLMClient is patched into the LLM factory, so each test
scripts exactly what "the model" answers and checks what the server did.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt import assistant  # noqa: E402
from jobhunt.approval import ApprovalState  # noqa: E402
from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.llm.anthropic_client import FakeLLMClient  # noqa: E402
from jobhunt.models import UserProfile  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _job(job_id: str, company: str, score: float, status: str = "Saved") -> dict:
    return {
        "job_id": job_id, "title": "Data Engineer", "company": company,
        "location": "Hyderabad", "url": f"https://example.com/{job_id}",
        "source": "fixture", "relevance_score": score, "status": status,
        "score_breakdown": {
            "total": score, "title": 1.0, "skills": 0.6, "seniority": 0.9,
            "location": 1.0, "matched_keywords": ["python", "sql"],
            "missing_keywords": ["airflow"], "skills_scored": True,
        },
        "events": [],
    }


def _state() -> DashboardState:
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.user_profile = UserProfile(
        user_id="u1", name="Sadat", email="s@example.com",
        target_roles=["Data Engineer"], locations=["Hyderabad"],
        skills=["python", "sql"],
        experiences=[{"title": "Junior Data Engineer", "company": "SAS2PY",
                      "start": "Nov 2025", "end": "Present",
                      "bullets": ["Built PySpark pipelines on Databricks."]}],
        application_answers={"work_authorization": "Indian citizen"},
    )
    state.jobs = [_job("j-amzn", "Amazon", 0.82), _job("j-goog", "Google", 0.7)]
    state.approval_queue.submit(job_id="j-goog", document_id="d1",
                                company="Google", title="Data Engineer")
    return state


@pytest.fixture
def llm(monkeypatch):
    """Script the model: set ``llm.reply`` to the raw text it should return."""
    class Box:
        reply = ""
        calls: list = []

    def respond(system: str, user: str) -> str:
        Box.calls.append((system, user))
        return Box.reply

    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env",
                        lambda: FakeLLMClient(respond))
    return Box


def _say(llm, reply: str = "ok", actions: list | None = None) -> None:
    llm.reply = json.dumps({"reply": reply, "actions": actions or []})


def _chat(client, text: str, **extra) -> dict:
    res = client.post("/api/assistant/chat",
                      json={"messages": [{"role": "user", "content": text}], **extra})
    assert res.status_code == 200, res.text
    return res.json()


def _setup():
    state = _state()
    return state, TestClient(create_app(state))


# ---------------------------------------------------------------- parsing

def test_parse_plain_json():
    reply, actions = assistant.parse_model_output('{"reply": "hi", "actions": [{"name": "x"}]}')
    assert reply == "hi" and actions == [{"name": "x"}]


def test_parse_fenced_json():
    raw = '```json\n{"reply": "fenced", "actions": []}\n```'
    assert assistant.parse_model_output(raw) == ("fenced", [])


def test_parse_json_with_surrounding_prose():
    raw = 'Sure! {"reply": "inner", "actions": []} hope that helps'
    assert assistant.parse_model_output(raw) == ("inner", [])


def test_parse_non_json_falls_back_to_text():
    assert assistant.parse_model_output("just words") == ("just words", [])


def test_non_json_model_output_is_the_reply(llm):
    state, client = _setup()
    llm.reply = "I am not JSON"
    out = _chat(client, "hello")
    assert out == {"reply": "I am not JSON", "applied": [], "pending": [], "llm": True}


def test_system_prompt_carries_state_and_catalog(llm):
    state, client = _setup()
    _say(llm)
    _chat(client, "hi")
    system, user = llm.calls[-1]
    assert "j-amzn | Data Engineer | Amazon | Saved | 82%" in system
    assert "set_screening_answers" in system and "approve_job" in system
    assert "work_authorization" in system
    assert "OWNER: hi" in user


def test_history_is_capped():
    msgs = [{"role": "user", "content": "x" * 1000} for _ in range(40)]
    kept = assistant._clean_messages(msgs)
    assert len(kept) <= assistant.MAX_MESSAGES
    assert sum(len(m["content"]) for m in kept) <= assistant.MAX_CHARS


# ---------------------------------------------------------------- no LLM

def test_no_llm_returns_enable_message(monkeypatch):
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: None)
    state, client = _setup()
    out = _chat(client, "hi")
    assert out["llm"] is False and out["applied"] == [] and out["pending"] == []
    assert "Claude Code" in out["reply"] and "claude" in out["reply"]


def test_llm_error_is_not_a_500(monkeypatch):
    def boom(system, user):
        raise RuntimeError("timed out")
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env",
                        lambda: FakeLLMClient(boom))
    state, client = _setup()
    out = _chat(client, "hi")
    assert "timed out" in out["reply"] and out["llm"] is False


# ---------------------------------------------------------------- safe actions

def test_update_profile(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "update_profile", "args": {
        "target_roles": ["Data Engineer", "Analytics Engineer"],
        "locations": "Hyderabad, Bengaluru", "remote_ok": False,
        "links": {"github": "https://github.com/sadat"}, "min_salary": 1200000}}])
    out = _chat(client, "Add Analytics Engineer and Bengaluru, github.com/sadat")
    assert out["applied"][0]["ok"], out
    p = state.user_profile
    assert p.target_roles == ["Data Engineer", "Analytics Engineer"]
    assert p.locations == ["Hyderabad", "Bengaluru"]
    assert p.remote_ok is False and p.min_salary == 1200000
    assert p.links["github"] == "https://github.com/sadat"


def test_update_profile_rehydrates_redacted_email(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "update_profile", "args": {"email": "<email>"}}])
    out = _chat(client, "my email is New.Me@Example.org")
    assert out["applied"][0]["ok"], out
    assert state.user_profile.email == "new.me@example.org"


def test_screening_answers_merge_and_keep_other_keys(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "set_screening_answers", "args": {
        "answers": {"notice_period": "30 days", "expected_ctc": "12 LPA"}}}])
    out = _chat(client, "Fill my screening answers: notice period 30 days, expected CTC 12 LPA")
    assert out["applied"][0]["ok"]
    assert state.user_profile.application_answers == {
        "work_authorization": "Indian citizen",
        "notice_period": "30 days", "expected_ctc": "12 LPA"}


def test_add_and_remove_skills(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "add_skills", "args": {"skills": ["Airflow", "dbt", "python"]}}])
    _chat(client, "Add Airflow and dbt to my skills")
    assert state.user_profile.skills == ["python", "sql", "airflow", "dbt"]
    _say(llm, actions=[{"name": "remove_skills", "args": {"skills": ["SQL"]}}])
    _chat(client, "drop sql")
    assert state.user_profile.skills == ["python", "airflow", "dbt"]


def test_set_job_status_by_company(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "set_job_status",
                        "args": {"company": "amazon", "status": "Interview"}}])
    out = _chat(client, "Mark Amazon as Interview")
    assert out["applied"][0]["ok"], out
    job = state.jobs[0]
    assert job["status"] == "Interview"
    assert job["events"][-1]["stage"] == "Interview"


def test_add_job_note(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "add_job_note", "args": {
        "job_id": "j-amzn", "note": "Recruiter called", "next_action": "Send availability"}}])
    _chat(client, "note on amazon")
    assert state.jobs[0]["notes"] == "Recruiter called"
    assert state.jobs[0]["next_action"] == "Send availability"


def test_reject_job(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "reject_job", "args": {"job_id": "j-goog"}}])
    out = _chat(client, "reject google")
    assert out["applied"][0]["ok"], out
    [req] = state.approval_queue.by_job("j-goog")
    assert req.state == ApprovalState.REJECTED


def test_set_autonomy_limits(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "set_autonomy_limits",
                        "args": {"daily_apply_cap": 8, "relevance_floor": 70}}])
    _chat(client, "cap 8 a day, floor 70%")
    assert state.user_profile.daily_apply_cap == 8
    assert state.user_profile.relevance_floor == pytest.approx(0.7)
    assert state.user_profile.auto_apply is False


def test_set_radar(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "set_radar", "args": {
        "radar_enabled": True, "current_title": "Junior DE", "radar_keywords": ["spark"]}}])
    _chat(client, "turn on radar")
    p = state.user_profile
    assert p.radar_enabled and p.current_title == "Junior DE" and p.radar_keywords == ["spark"]


def test_explain_match_does_not_mutate(llm):
    state, client = _setup()
    before = json.dumps(state.jobs, sort_keys=True)
    _say(llm, actions=[{"name": "explain_match", "args": {"job_id": "j-amzn"}}])
    out = _chat(client, "Why is my top job a 82% match?")
    assert out["applied"][0]["ok"]
    assert "82%" in out["applied"][0]["detail"] and "airflow" in out["applied"][0]["detail"]
    assert json.dumps(state.jobs, sort_keys=True) == before


def test_applied_actions_reach_the_reasoning_feed(llm):
    state, client = _setup()
    seen = []
    orig = state.bus.publish
    state.bus.publish = lambda agent, task, thought, **kw: (seen.append(agent),
                                                            orig(agent, task, thought, **kw))
    _say(llm, actions=[{"name": "add_skills", "args": {"skills": ["dbt"]}}])
    _chat(client, "add dbt")
    assert "assistant" in seen


# ---------------------------------------------------------------- confirmation

def test_gated_actions_are_pending_until_confirmed(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "approve_job", "args": {"job_id": "j-goog"}},
                       {"name": "toggle_auto_apply", "args": {"enabled": True}}])
    out = _chat(client, "approve google and turn on auto apply")
    assert out["applied"] == []
    assert [a["name"] for a in out["pending"]] == ["approve_job", "toggle_auto_apply"]
    assert state.user_profile.auto_apply is False
    [req] = state.approval_queue.by_job("j-goog")
    assert req.state == ApprovalState.PENDING

    out = _chat(client, "approve google and turn on auto apply", confirm=out["pending"])
    assert all(a["ok"] for a in out["applied"]), out
    assert state.user_profile.auto_apply is True
    assert req.state == ApprovalState.APPROVED
    assert state.jobs[1]["status"] == "Applied"


def test_fetch_jobs_is_gated(llm, monkeypatch):
    calls = []
    monkeypatch.setattr("jobhunt.dashboard.server._discover_once",
                        lambda state, registry: calls.append(1) or {"added": 3})
    state, client = _setup()
    _say(llm, actions=[{"name": "fetch_jobs", "args": {}}])
    out = _chat(client, "find new jobs")
    assert out["pending"] and not calls
    out = _chat(client, "find new jobs", confirm=out["pending"])
    assert calls == [1] and "3 new" in out["applied"][0]["detail"]


def test_bulk_job_change_needs_confirmation(llm):
    state, client = _setup()
    state.jobs = [_job(f"j{i}", f"Co{i}", 0.5) for i in range(7)]
    _say(llm, actions=[{"name": "set_job_status",
                        "args": {"job_ids": [f"j{i}" for i in range(7)], "status": "Closed"}}])
    out = _chat(client, "close everything")
    assert out["applied"] == [] and len(out["pending"]) == 1
    assert all(j["status"] == "Saved" for j in state.jobs)
    _chat(client, "close everything", confirm=out["pending"])
    assert all(j["status"] == "Closed" for j in state.jobs)


def test_edit_experience_with_stated_numbers(llm):
    state, client = _setup()
    text = "Change my first bullet to: Cut pipeline runtime by 40% across 12 jobs"
    _say(llm, actions=[{"name": "edit_experience", "args": {"index": 0, "fields": {
        "bullets": ["Cut pipeline runtime by 40% across 12 jobs."]}}}])
    out = _chat(client, text)
    assert out["pending"] and state.user_profile.experiences[0]["bullets"][0].startswith("Built")
    out = _chat(client, text, confirm=out["pending"])
    assert out["applied"][0]["ok"], out
    assert state.user_profile.experiences[0]["bullets"] == [
        "Cut pipeline runtime by 40% across 12 jobs."]
    assert state.user_profile.experiences[0]["company"] == "SAS2PY"


def test_edit_experience_rejects_invented_numbers(llm):
    state, client = _setup()
    _say(llm, actions=[{"name": "edit_experience", "args": {"index": 0, "fields": {
        "bullets": ["Cut pipeline runtime by 65% for 3M rows."]}}}])
    out = _chat(client, "Make my bullet sound more impressive")
    assert out["pending"] == []
    assert out["applied"][0]["ok"] is False and "65" in out["applied"][0]["detail"]
    # Even a forged confirm is re-checked against the owner's messages.
    out = _chat(client, "Make my bullet sound more impressive", confirm=[
        {"name": "edit_experience", "args": {"index": 0, "fields": {
            "bullets": ["Cut pipeline runtime by 65%."]}}}])
    assert out["applied"][0]["ok"] is False
    assert state.user_profile.experiences[0]["bullets"] == [
        "Built PySpark pipelines on Databricks."]


# ---------------------------------------------------------------- invalid

@pytest.mark.parametrize("action", [
    {"name": "delete_everything", "args": {}},
    {"name": "set_job_status", "args": {"job_id": "j-amzn", "status": "Hired"}},
    {"name": "set_job_status", "args": {"job_id": "nope", "status": "Applied"}},
    {"name": "update_profile", "args": {"email": "not-an-email"}},
    {"name": "update_profile", "args": {"password": "x"}},
    {"name": "add_skills", "args": "airflow"},
    {"name": "set_autonomy_limits", "args": {"daily_apply_cap": "lots"}},
    {"name": "edit_experience", "args": {"index": 9, "fields": {"title": "CTO"}}},
    {"name": "reject_job", "args": {"job_id": "j-amzn"}},  # nothing awaiting approval
    "not even an object",
    {"args": {}},
])
def test_invalid_actions_are_rejected_without_raising(llm, action):
    state, client = _setup()
    before = (state.user_profile.to_dict(), json.dumps(state.jobs, sort_keys=True))
    llm.reply = json.dumps({"reply": "trying", "actions": [action]})
    out = _chat(client, "do it")
    assert out["reply"] == "trying" and out["pending"] == []
    assert len(out["applied"]) == 1 and out["applied"][0]["ok"] is False
    assert (state.user_profile.to_dict(), json.dumps(state.jobs, sort_keys=True)) == before


def test_bad_request_bodies_do_not_500(llm):
    state, client = _setup()
    _say(llm)
    for body in ({}, {"messages": "x"}, {"messages": [{"role": "system", "content": "x"}]},
                 {"messages": [], "confirm": "nope"}):
        assert client.post("/api/assistant/chat", json=body).status_code == 200


def test_profile_actions_without_profile(llm):
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))
    _say(llm, actions=[{"name": "add_skills", "args": {"skills": ["dbt"]}}])
    out = _chat(client, "add dbt")
    assert out["applied"][0]["ok"] is False and "onboarding" in out["applied"][0]["detail"]
