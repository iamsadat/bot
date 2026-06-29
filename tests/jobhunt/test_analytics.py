"""Tests for outcome analytics: A/B variant assignment + attribution (E4)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.ab import ExperimentRegistry  # noqa: E402
from jobhunt.dashboard.persistence import DashboardStore  # noqa: E402
from jobhunt.dashboard.inbox_sync import sync_inbox  # noqa: E402
from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, _RESUME_EXPERIMENT_NAME, _RESUME_VARIANTS, _assign_variant,
    _record_outcome, create_app,
)
from jobhunt.inbox.sources import FakeInboxSource, InboxMessage  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _client(state: DashboardState | None = None):
    state = state or _state()
    return state, TestClient(create_app(state))


# --------------------------------------------------------------- _assign_variant

def test_assign_variant_stores_variant_on_doc():
    state = _state()
    doc = {"job_id": "j1", "company": "Acme", "title": "Backend"}
    variant = _assign_variant(state, doc)
    assert variant in _RESUME_VARIANTS
    assert doc["variant"] == variant


def test_assign_variant_is_deterministic_per_job():
    state = _state()
    doc1 = {"job_id": "same-job"}
    doc2 = {"job_id": "same-job"}
    v1 = _assign_variant(state, doc1)
    v2 = _assign_variant(state, doc2)
    assert v1 == v2


def test_assign_variant_records_an_impression():
    state = _state()
    doc = {"job_id": "j1"}
    variant_name = _assign_variant(state, doc)
    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    assert exp is not None
    v = next(v for v in exp.variants if v.name == variant_name)
    assert v.impressions == 1
    assert v.successes == 0


def test_persist_tailored_docs_assigns_variant():
    from jobhunt.agents.resume import TailoredDocument

    state = _state()
    doc = TailoredDocument(
        job_id="j1", company="Acme", title="Backend", url="https://acme.test/j1",
        resume_text="resume", cover_letter_text="cover",
        keyword_coverage=0.8, matched_keywords=["python"], missing_keywords=[],
        bullets=[],
    )
    from jobhunt.dashboard.server import _persist_tailored_docs
    new = _persist_tailored_docs(state, [doc])
    assert new == 1
    assert "variant" in state.documents["j1"]
    assert state.documents["j1"]["variant"] in _RESUME_VARIANTS


# --------------------------------------------------------------- _record_outcome

def test_record_outcome_increments_success_for_assigned_variant():
    state = _state()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                   "status": "Applied", "events": []}]
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    variant_name = _assign_variant(state, doc)

    _record_outcome(state, "j1")

    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    v = next(v for v in exp.variants if v.name == variant_name)
    assert v.impressions == 1
    assert v.successes == 1


def test_record_outcome_noop_without_document():
    state = _state()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                   "status": "Applied", "events": []}]
    # No document for j1 — must not raise.
    _record_outcome(state, "j1")
    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    assert all(v.impressions == 0 for v in exp.variants)


def test_record_outcome_noop_without_variant_on_doc():
    state = _state()
    state.documents["j1"] = {"job_id": "j1"}  # no "variant" key
    _record_outcome(state, "j1")  # must not raise
    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    assert all(v.impressions == 0 for v in exp.variants)


def test_status_endpoint_records_outcome_on_interview():
    state, client = _client()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                   "status": "Applied", "events": []}]
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    variant_name = _assign_variant(state, doc)

    resp = client.post("/api/jobs/j1/status", json={"status": "Interview"})
    assert resp.status_code == 200

    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    v = next(v for v in exp.variants if v.name == variant_name)
    assert v.successes == 1


def test_status_endpoint_does_not_record_outcome_for_non_terminal_status():
    state, client = _client()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                   "status": "Saved", "events": []}]
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    _assign_variant(state, doc)

    resp = client.post("/api/jobs/j1/status", json={"status": "Applied"})
    assert resp.status_code == 200

    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    assert all(v.successes == 0 for v in exp.variants)


def test_inbox_sync_records_outcome_on_interview_email():
    state = _state()
    state.jobs = [{"job_id": "j1", "company": "Acme Robotics", "title": "Backend",
                   "status": "Applied", "events": []}]
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    variant_name = _assign_variant(state, doc)

    msg = InboxMessage(message_id="1", subject="Interview invitation",
                       body="Let's schedule a call", sender="r@acmerobotics.com",
                       received_at=1.0, company="Acme Robotics")
    res = sync_inbox(state, FakeInboxSource([msg]))
    assert res["updates"] == 1
    assert state.jobs[0]["status"] == "Interview"

    exp = state.experiments.get(_RESUME_EXPERIMENT_NAME)
    v = next(v for v in exp.variants if v.name == variant_name)
    assert v.successes == 1


# --------------------------------------------------------------- GET /api/analytics

def test_analytics_endpoint_shape():
    _, client = _client()
    resp = client.get("/api/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert "funnel" in body
    assert "experiment" in body
    assert "winner" in body
    assert "variants" in body
    names = {v["name"] for v in body["variants"]}
    assert names == set(_RESUME_VARIANTS)
    for v in body["variants"]:
        assert "impressions" in v and "successes" in v and "success_rate" in v


def test_analytics_endpoint_reflects_recorded_outcomes():
    state, client = _client()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend",
                   "status": "Applied", "events": []}]
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    variant_name = _assign_variant(state, doc)
    _record_outcome(state, "j1")

    body = client.get("/api/analytics").json()
    variant_row = next(v for v in body["variants"] if v["name"] == variant_name)
    assert variant_row["impressions"] == 1
    assert variant_row["successes"] == 1
    assert variant_row["success_rate"] == pytest.approx(1.0)


# --------------------------------------------------------------- persistence

def test_experiments_default_to_dict_round_trips(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    doc = {"job_id": "j1"}
    state.documents["j1"] = doc
    _assign_variant(state, doc)
    state.persist()

    restored = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    restored.restore()

    exp = restored.experiments.get(_RESUME_EXPERIMENT_NAME)
    assert exp is not None
    assert sum(v.impressions for v in exp.variants) == 1


def test_experiments_snapshot_round_trip_via_store(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    registry = ExperimentRegistry()
    from jobhunt.ab import Experiment, Variant
    exp = Experiment(name="resume_strategy", target="resume", variants=[
        Variant("concise", {}), Variant("keyword_rich", {}), Variant("impact_first", {}),
    ])
    exp.record("concise", success=True)
    exp.record("concise", success=False)
    registry.register(exp)

    store.save(profile=None, jobs=[], applications=[], approvals=[], plan=None,
               hunt_status="idle", experiments=registry.to_dict())
    snap = store.load()
    restored = ExperimentRegistry.from_dict(snap["experiments"])
    restored_exp = restored.get("resume_strategy")
    assert restored_exp is not None
    concise = next(v for v in restored_exp.variants if v.name == "concise")
    assert concise.impressions == 2
    assert concise.successes == 1
