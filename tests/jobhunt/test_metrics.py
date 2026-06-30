"""Tests for the funnel/streak metrics (M0) + activity_days persistence."""

from __future__ import annotations

from datetime import date, timedelta

from jobhunt.dashboard.persistence import DashboardStore
from jobhunt.dashboard.server import DashboardState
from jobhunt.metrics import compute_funnel
from jobhunt.models import UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _job(jid, status, events=None):
    return {"job_id": jid, "company": "Acme", "title": "Backend",
            "location": "Remote", "status": status, "events": events or []}


def _iso(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


def test_funnel_counts_stages():
    st = _state()
    st.jobs = [
        _job("a", "Saved"),
        _job("b", "Applied"),
        _job("c", "Assessment"),
        _job("d", "Interview"),
        _job("e", "Offer"),
        _job("f", "Closed"),
    ]
    st.documents = {
        "b": {"keyword_coverage": 0.8},
        "c": {"keyword_coverage": 0.6},
    }
    f = compute_funnel(st)
    assert f["discovered"] == 6
    assert f["tailored"] == 2
    # Applied/Assessment/Interview/Offer/Closed all count as applied.
    assert f["applied"] == 5
    assert f["interview"] == 1
    assert f["offer"] == 1
    assert f["evidence_coverage"] == 0.7


def test_callback_rate_guards_zero_applied():
    st = _state()
    f = compute_funnel(st)
    assert f["callback_rate"] == 0.0  # no ZeroDivisionError
    assert f["evidence_coverage"] == 0.0  # no documents


def test_callback_rate_ratio():
    st = _state()
    st.jobs = [_job("a", "Applied"), _job("b", "Applied"),
               _job("c", "Applied"), _job("d", "Interview")]
    f = compute_funnel(st)
    # 1 interview / 4 applied = 0.25
    assert f["applied"] == 4 and f["interview"] == 1
    assert f["callback_rate"] == 0.25


def test_interview_counted_via_event_without_status():
    st = _state()
    st.jobs = [_job("a", "Closed", events=[{"stage": "Interview", "ts": 0}])]
    f = compute_funnel(st)
    assert f["interview"] == 1


def test_streak_consecutive_days_ending_today():
    st = _state()
    st.activity_days = [_iso(2), _iso(1), _iso(0)]
    assert compute_funnel(st)["streak"] == 3


def test_streak_zero_when_today_missing():
    st = _state()
    st.activity_days = [_iso(2), _iso(1)]  # yesterday/2-days-ago, not today
    assert compute_funnel(st)["streak"] == 0


def test_streak_stops_at_gap():
    st = _state()
    st.activity_days = [_iso(3), _iso(1), _iso(0)]  # gap at day 2
    assert compute_funnel(st)["streak"] == 2


def test_applied_this_week_trailing_window():
    st = _state()
    st.activity_days = [_iso(10), _iso(6), _iso(3), _iso(0)]  # only last 3 within 7d
    f = compute_funnel(st)
    assert f["applied_this_week"] == 3


def test_weekly_target_default_and_from_profile():
    st = _state()
    assert compute_funnel(st)["weekly_target"] == 10  # default when no profile
    st.user_profile = UserProfile(user_id="u", name="A", email="a@x.com",
                                  target_roles=[], locations=[], weekly_target=4)
    st.activity_days = [_iso(0), _iso(1)]
    f = compute_funnel(st)
    assert f["weekly_target"] == 4
    assert f["applied_this_week"] == 2
    assert f["weekly_progress"] == 0.5


def test_activity_days_default_empty_and_persists_round_trip(tmp_path):
    st = _state()
    assert st.activity_days == []
    store = DashboardStore(tmp_path / "t.db")
    store.save(profile=None, jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle", activity_days=["2026-06-29"])
    snap = store.load()
    assert snap["activity_days"] == ["2026-06-29"]
