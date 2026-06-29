"""Tests for the activity digest (R2): content, endpoint, and loop gating."""

from __future__ import annotations

import time
from datetime import date

import pytest

from jobhunt.dashboard.server import DashboardState
from jobhunt.digest import build_digest
from jobhunt.notify import Notifier, WebhookSink
from jobhunt.submitters.base import FakePoster
from jobhunt.trace import ThoughtBus, TraceStore


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _job(jid, status, events=None):
    return {"job_id": jid, "company": "Acme", "title": "Backend",
            "location": "Remote", "status": status, "events": events or []}


def test_build_digest_content():
    st = _state()
    st.jobs = [
        _job("a", "Saved", events=[{"stage": "Discovered", "ts": time.time()}]),
        _job("b", "Applied"),
        _job("c", "Interview"),
    ]
    st.activity_days = [date.today().isoformat()]
    d = build_digest(st)
    assert set(d) == {"subject", "body"}
    assert "digest" in d["subject"].lower()
    assert "1 new match" in d["body"]
    assert "Applied: 1" in d["body"]
    assert "Interview: 1" in d["body"]
    assert "Applied this week: 1" in d["body"]
    assert "Current streak: 1 day" in d["body"]


def test_build_digest_recent_window_excludes_old_matches():
    st = _state()
    old = time.time() - 2 * 86400  # 2 days ago
    new = time.time()
    st.jobs = [
        _job("a", "Saved", events=[{"stage": "Discovered", "ts": old}]),
        _job("b", "Saved", events=[{"stage": "Discovered", "ts": new}]),
    ]
    # Daily window (24h) → only the fresh one counts.
    assert "1 new match" in build_digest(st, period="daily")["body"]
    # Weekly window (7d) → both count.
    assert "2 new match" in build_digest(st, period="weekly")["body"]


def test_digest_endpoint_without_notifier():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jobhunt.dashboard.server import create_app

    st = _state()
    c = TestClient(create_app(st))
    r = c.post("/api/digest/send")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sent"] is False
    assert "subject" in body and "body" in body


def test_digest_endpoint_with_notifier_sends():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jobhunt.dashboard.server import create_app

    st = _state()
    poster = FakePoster({"http://hook": (200, {})})
    st.notifier = Notifier([WebhookSink("http://hook", poster)])
    c = TestClient(create_app(st))
    r = c.post("/api/digest/send")
    assert r.json()["sent"] is True
    assert poster.calls and poster.calls[0]["body"]["kind"] == "digest"


def test_digest_loop_off_by_default(monkeypatch):
    """Default (env unset) → no digest task; lifespan enters/exits cleanly."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jobhunt.dashboard.server import create_app

    monkeypatch.delenv("JOBHUNT_DIGEST_INTERVAL_SECONDS", raising=False)
    st = _state()
    # Entering the context starts the lifespan; with the env unset no digest
    # loop task is created, so this completes immediately without hanging.
    with TestClient(create_app(st)) as c:
        assert c.get("/api/status").status_code == 200
