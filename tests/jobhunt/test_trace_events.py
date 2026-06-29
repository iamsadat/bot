"""Tests for substantive structured reasoning trace events."""

from __future__ import annotations

import json

from jobhunt.agents.resume import ResumeArchitectAgent, ResumeInputs
from jobhunt.models import JobPosting, ReasoningTrace, TraceEvent
from jobhunt.trace import TraceStore


def _posting():
    return JobPosting(
        job_id="j1", source="g", source_id="1",
        url="https://boards.greenhouse.io/co/j1",
        title="Backend Engineer", company="C", location="Remote",
        jd_text="Python, Kubernetes, PostgreSQL, Redis, Rust, Elixir.", remote=True,
    )


def test_resume_agent_emits_considered_and_rejected(profile, store, bus):
    agent = ResumeArchitectAgent(store, bus)
    res = agent.run(ResumeInputs(profile=profile, postings=[_posting()]), task_id="t")
    act_events = [e for e in res.trace.events if e.phase == "act"]
    assert act_events
    ev = act_events[0]
    assert ev.considered  # matched keywords
    assert ev.confidence is not None
    # Rust/Elixir aren't in the profile → rejected with a reason.
    assert any("evidence" in r["reason"] for r in ev.rejected)


def test_run_auto_emits_critique_and_decide(profile, store, bus):
    agent = ResumeArchitectAgent(store, bus)
    res = agent.run(ResumeInputs(profile=profile, postings=[_posting()]), task_id="t")
    phases = {e.phase for e in res.trace.events}
    assert "critique" in phases
    assert "decide" in phases
    decide = next(e for e in res.trace.events if e.phase == "decide")
    assert decide.confidence is not None


def test_bus_payload_carries_phase_and_thought(profile, store, bus):
    agent = ResumeArchitectAgent(store, bus)
    agent.run(ResumeInputs(profile=profile, postings=[_posting()]), task_id="t")
    hist = bus.history()
    assert hist
    structured = [h for h in hist if "phase" in h]
    assert structured
    # Back-compat: the old flat "thought" key is still present on every event.
    assert all("thought" in h for h in hist)


def test_trace_store_redacts_event_fields():
    store = TraceStore()
    tr = ReasoningTrace.new("x", "t")
    tr.events.append(TraceEvent(
        phase="act", summary="contact ada@example.com or +1 555 123 4567",
        considered=["email ada@example.com"],
        rejected=[{"item": "x", "reason": "call +1 555 999 8888"}],
    ))
    store.append(tr)
    ev = tr.events[0]
    assert "ada@example.com" not in ev.summary
    assert "<email>" in ev.summary
    assert "<email>" in ev.considered[0]
    assert "<phone>" in ev.rejected[0]["reason"]


def test_to_jsonl_serializes_events():
    store = TraceStore()
    tr = ReasoningTrace.new("x", "t")
    tr.events.append(TraceEvent(phase="decide", summary="done", confidence=0.9))
    store.append(tr)
    line = store.to_jsonl()
    parsed = json.loads(line)
    assert parsed["events"][0]["phase"] == "decide"
    assert parsed["events"][0]["confidence"] == 0.9
