"""Tests for the waitlist + pricing-preference store and its endpoints.

The stats endpoint is admin-gated (it exposes aggregate willingness-to-pay
data), so the endpoint tests here also cover that gate.
"""

from __future__ import annotations

import pytest

from jobhunt.dashboard.waitlist import WaitlistStore

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


# --------------------------------------------------------------------- store

def test_join_upsert_and_counts(tmp_path):
    store = WaitlistStore(db_path=tmp_path / "w.db")
    store.join("a@x.com", "lifetime_99", "2026-07-01")
    store.join("b@x.com", "monthly_19", "2026-07-01")
    store.join("a@x.com", "monthly_29", "2026-07-02")  # resubmit updates pref

    c = store.counts()
    assert c["total"] == 2
    assert c["by_price_pref"]["monthly_29"] == 1
    assert c["by_price_pref"]["lifetime_99"] == 0


# ----------------------------------------------------------------- endpoints

_ADMIN_TOKEN = "test-admin-token"
_ADMIN = {"X-Admin-Token": _ADMIN_TOKEN}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_WAITLIST_DB_PATH", str(tmp_path / "waitlist.db"))
    monkeypatch.setenv("JOBHUNT_ADMIN_TOKEN", _ADMIN_TOKEN)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return TestClient(create_app(state))


def test_join_waitlist_then_stats(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/waitlist", json={"email": "a@x.com", "price_pref": "lifetime_99"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    body = client.get("/api/waitlist/stats", headers=_ADMIN).json()
    assert body["total"] == 1
    assert body["by_price_pref"]["lifetime_99"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "price_pref": "lifetime_99"},
        {"email": "a@x.com", "price_pref": "monthly_999"},
        {"email": "a@x.com"},
        {},
    ],
)
def test_join_waitlist_rejects_bad_input(tmp_path, monkeypatch, payload):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/waitlist", json=payload).status_code == 400


def test_joining_stays_unauthenticated(tmp_path, monkeypatch):
    """Signing up is public by design — only the aggregate read is gated."""
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("JOBHUNT_ADMIN_TOKEN")
    r = client.post(
        "/api/waitlist", json={"email": "a@x.com", "price_pref": "monthly_19"},
    )
    assert r.status_code == 200


# ----------------------------------------------------------------- admin gate

def test_waitlist_stats_requires_admin_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/waitlist/stats").status_code == 403


def test_waitlist_stats_rejects_wrong_admin_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/waitlist/stats", headers={"X-Admin-Token": "nope"})
    assert r.status_code == 403


def test_waitlist_stats_closed_when_admin_token_unset(tmp_path, monkeypatch):
    """An unset server token must mean "closed", never "open to everyone"."""
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("JOBHUNT_ADMIN_TOKEN")
    assert client.get("/api/waitlist/stats").status_code == 403
    r = client.get("/api/waitlist/stats", headers={"X-Admin-Token": ""})
    assert r.status_code == 403
