"""Tests for Career Radar (retention alerts) + market-value tracker."""

from __future__ import annotations

from jobhunt.dashboard.persistence import DashboardStore
from jobhunt.dashboard.server import (
    DashboardState, _is_radar_hit, _update_market_value,
)
from jobhunt.models import UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _profile(**overrides) -> UserProfile:
    base = dict(
        user_id="u1", name="Ada", email="ada@x.com",
        target_roles=["backend engineer"], locations=["Remote"],
        radar_enabled=True,
    )
    base.update(overrides)
    return UserProfile(**base)


def _job(**overrides) -> dict:
    base = dict(title="Backend Engineer", company="Acme", location="Remote",
                relevance_score=0.5, salary_min=None, salary_max=None)
    base.update(overrides)
    return base


# --------------------------------------------------------------- _is_radar_hit

def test_radar_hit_when_salary_beats_current():
    p = _profile(current_salary=150_000)
    job = _job(salary_max=180_000)
    assert _is_radar_hit(p, job) is True


def test_radar_no_hit_when_salary_does_not_beat_current():
    p = _profile(current_salary=150_000)
    job = _job(salary_min=120_000, salary_max=140_000)
    assert _is_radar_hit(p, job) is False


def test_radar_hit_on_keyword_in_title():
    p = _profile(radar_keywords=["platform"])
    job = _job(title="Platform Engineer")
    assert _is_radar_hit(p, job) is True


def test_radar_hit_on_keyword_in_document_keywords():
    p = _profile(radar_keywords=["rust"])
    job = _job(title="Backend Engineer")
    doc = {"matched_keywords": ["python", "rust"], "missing_keywords": []}
    assert _is_radar_hit(p, job, doc) is True


def test_radar_no_hit_below_relevance_threshold():
    p = _profile(current_title="Backend Engineer")
    job = _job(title="Senior Backend Engineer", relevance_score=0.4)
    assert _is_radar_hit(p, job) is False


def test_radar_disabled_never_hits():
    p = _profile(radar_enabled=False, current_salary=100_000)
    job = _job(salary_max=999_000, title="Senior Backend Engineer", relevance_score=0.9)
    assert _is_radar_hit(p, job) is False


def test_radar_hit_on_step_up_title():
    p = _profile(current_title="Backend Engineer")
    job = _job(title="Senior Backend Engineer", relevance_score=0.6)
    assert _is_radar_hit(p, job) is True


def test_radar_no_hit_when_already_at_step_up_level():
    p = _profile(current_title="Senior Backend Engineer")
    job = _job(title="Senior Backend Engineer", relevance_score=0.9)
    assert _is_radar_hit(p, job) is False


# --------------------------------------------------------- _update_market_value

class _FakeEstimate:
    def __init__(self, role, location, median=160_000, currency="USD"):
        self.role, self.location = role, location
        self.currency, self.median = currency, median
        self.p10, self.p90, self.sample = 100_000, 220_000, 50


class _FakeSalaryClient:
    def __init__(self):
        self.calls = []

    def estimate(self, role, location=""):
        self.calls.append((role, location))
        return _FakeEstimate(role, location)


def test_update_market_value_appends_entry():
    st = _state()
    st.user_profile = _profile()
    client = _FakeSalaryClient()
    assert _update_market_value(st, client) is True
    assert len(st.market_value) == 1
    entry = st.market_value[0]
    assert entry["median"] == 160_000
    assert entry["currency"] == "USD"
    assert entry["role"] == "backend engineer"
    assert "date" in entry


def test_update_market_value_dedupes_same_day():
    st = _state()
    st.user_profile = _profile()
    client = _FakeSalaryClient()
    assert _update_market_value(st, client) is True
    assert _update_market_value(st, client) is False
    assert len(st.market_value) == 1
    assert len(client.calls) == 1


def test_update_market_value_noop_without_client():
    st = _state()
    st.user_profile = _profile()
    assert _update_market_value(st, None) is False
    assert st.market_value == []


def test_update_market_value_noop_without_target_roles():
    st = _state()
    st.user_profile = _profile(target_roles=[])
    assert _update_market_value(st, _FakeSalaryClient()) is False


def test_update_market_value_noop_without_profile():
    st = _state()
    assert _update_market_value(st, _FakeSalaryClient()) is False


# --------------------------------------------------------------- persistence

def test_market_value_default_empty(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    p = _profile()
    store.save(profile=p, jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle")
    snap = store.load()
    assert snap["market_value"] == []


def test_market_value_round_trips(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    p = _profile()
    entries = [{"date": "2026-06-01", "median": 160_000, "currency": "USD",
                "role": "backend engineer"}]
    store.save(profile=p, jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle", market_value=entries)
    snap = store.load()
    assert snap["market_value"] == entries
    assert snap["profile"].radar_enabled is True


def test_profile_radar_fields_round_trip(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    p = _profile(current_salary=175_000, current_title="Backend Engineer",
                 radar_keywords=["staff", "platform"])
    store.save(profile=p, jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle")
    snap = store.load()
    restored = snap["profile"]
    assert restored.radar_enabled is True
    assert restored.current_salary == 175_000
    assert restored.current_title == "Backend Engineer"
    assert restored.radar_keywords == ["staff", "platform"]


# ------------------------------------------------------------------- HTTP API

import pytest  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import create_app  # noqa: E402
from jobhunt.onboarding import build_user_profile  # noqa: E402


def _client_with_profile():
    state = _state()
    state.user_profile = build_user_profile({
        "name": "Ada Lovelace", "email": "ada@example.com",
        "target_roles": ["backend engineer"], "locations": ["Remote"],
    })
    return state, TestClient(create_app(state))


def test_radar_settings_round_trip():
    state, client = _client_with_profile()
    got = client.get("/api/radar/settings")
    assert got.status_code == 200
    assert got.json() == {
        "radar_enabled": False, "current_salary": None,
        "current_title": "", "radar_keywords": [],
    }

    posted = client.post("/api/radar/settings", json={
        "radar_enabled": True, "current_salary": 165_000,
        "current_title": "Backend Engineer", "radar_keywords": ["staff", "lead"],
    })
    assert posted.status_code == 200
    body = posted.json()
    assert body["ok"] is True
    assert body["radar_enabled"] is True
    assert body["current_salary"] == 165_000
    assert body["radar_keywords"] == ["staff", "lead"]

    got2 = client.get("/api/radar/settings")
    assert got2.json()["current_title"] == "Backend Engineer"
    assert state.user_profile.radar_enabled is True


def test_radar_settings_requires_profile():
    state = _state()
    client = TestClient(create_app(state))
    assert client.get("/api/radar/settings").status_code == 400
    assert client.post("/api/radar/settings", json={"radar_enabled": True}).status_code == 400


def test_get_radar_shape():
    state, client = _client_with_profile()
    state.jobs = [
        {"job_id": "j1", "title": "Senior Backend Engineer", "company": "Acme",
         "status": "Saved", "radar_hit": True},
        {"job_id": "j2", "title": "Backend Engineer", "company": "Globex",
         "status": "Saved"},
    ]
    state.market_value = [{"date": "2026-06-01", "median": 160_000,
                           "currency": "USD", "role": "backend engineer"}]
    resp = client.get("/api/radar")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["market_value"] == state.market_value
    assert len(body["hits"]) == 1
    assert body["hits"][0]["job_id"] == "j1"
