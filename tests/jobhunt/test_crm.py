"""Tests for the Career CRM (contacts) + follow-up nudges."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.persistence import DashboardStore  # noqa: E402
from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.notify import NotificationEvent, Notifier  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _client(state: DashboardState | None = None):
    state = state or _state()
    return state, TestClient(create_app(state))


class _FakeSink:
    name = "fake"

    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def send(self, event: NotificationEvent) -> bool:
        self.events.append(event)
        return True


# --------------------------------------------------------------------- upsert

def test_create_contact_generates_id():
    state, client = _client()
    resp = client.post("/api/contacts", json={
        "name": "Jane Recruiter", "email": "jane@acme.com", "company": "Acme",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    contact = body["contact"]
    assert contact["id"]
    assert contact["name"] == "Jane Recruiter"
    assert contact["email"] == "jane@acme.com"
    assert len(state.contacts) == 1


def test_create_contact_requires_email():
    _, client = _client()
    resp = client.post("/api/contacts", json={"name": "No Email"})
    assert resp.status_code == 422


def test_upsert_updates_existing_contact_by_id():
    state, client = _client()
    created = client.post("/api/contacts", json={
        "name": "Jane Recruiter", "email": "jane@acme.com",
    }).json()["contact"]

    updated = client.post("/api/contacts", json={
        "id": created["id"], "name": "Jane R.", "email": "jane@acme.com",
        "notes": "met at conference",
    }).json()["contact"]

    assert updated["id"] == created["id"]
    assert updated["name"] == "Jane R."
    assert updated["notes"] == "met at conference"
    assert len(state.contacts) == 1  # update, not a second row


def test_upsert_persists():
    state, client = _client()
    client.post("/api/contacts", json={"name": "Jane", "email": "jane@acme.com"})
    assert len(state.contacts) == 1


# --------------------------------------------------------------------- list

def test_list_contacts_returns_all():
    _, client = _client()
    client.post("/api/contacts", json={"name": "A", "email": "a@x.com"})
    client.post("/api/contacts", json={"name": "B", "email": "b@x.com"})
    resp = client.get("/api/contacts")
    assert resp.status_code == 200
    assert len(resp.json()["contacts"]) == 2


def test_due_filter_only_returns_overdue_followups():
    _, client = _client()
    # Overdue
    client.post("/api/contacts", json={
        "name": "Overdue", "email": "od@x.com", "next_followup": "2020-01-01",
    })
    # Future
    client.post("/api/contacts", json={
        "name": "Future", "email": "fut@x.com", "next_followup": "2999-01-01",
    })
    # None
    client.post("/api/contacts", json={"name": "NoFollowup", "email": "none@x.com"})

    resp = client.get("/api/contacts", params={"due": "true"})
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()["contacts"]}
    assert names == {"Overdue"}


def test_due_filter_false_returns_all():
    _, client = _client()
    client.post("/api/contacts", json={
        "name": "Overdue", "email": "od@x.com", "next_followup": "2020-01-01",
    })
    client.post("/api/contacts", json={"name": "NoFollowup", "email": "none@x.com"})
    resp = client.get("/api/contacts")
    assert len(resp.json()["contacts"]) == 2


# -------------------------------------------------------------------- delete

def test_delete_contact_removes_it():
    state, client = _client()
    created = client.post("/api/contacts", json={
        "name": "Jane", "email": "jane@x.com",
    }).json()["contact"]
    resp = client.delete(f"/api/contacts/{created['id']}")
    assert resp.status_code == 200
    assert state.contacts == []


def test_delete_unknown_contact_404s():
    _, client = _client()
    resp = client.delete("/api/contacts/does-not-exist")
    assert resp.status_code == 404


# --------------------------------------------------------------------- nudge

def test_nudge_fires_notification():
    state, client = _client()
    sink = _FakeSink()
    state.notifier = Notifier([sink])
    created = client.post("/api/contacts", json={
        "name": "Jane Recruiter", "email": "jane@acme.com", "company": "Acme",
    }).json()["contact"]

    resp = client.post(f"/api/contacts/{created['id']}/nudge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["draft"] is None  # no job_id on this contact
    assert len(sink.events) == 1
    assert "Jane Recruiter" in sink.events[0].title


def test_nudge_returns_draft_when_job_id_present():
    state, client = _client()
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend Engineer",
                   "status": "Applied", "events": []}]
    state.documents["j1"] = {
        "job_id": "j1", "company": "Acme", "title": "Backend Engineer",
    }
    created = client.post("/api/contacts", json={
        "name": "Jane Recruiter", "email": "jane@acme.com", "job_id": "j1",
    }).json()["contact"]

    resp = client.post(f"/api/contacts/{created['id']}/nudge")
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    assert draft is not None
    assert "subject" in draft and "body" in draft
    assert "Backend Engineer" in draft["subject"]


def test_nudge_unknown_contact_404s():
    _, client = _client()
    resp = client.post("/api/contacts/nope/nudge")
    assert resp.status_code == 404


# --------------------------------------------------------------- persistence

def test_contacts_round_trip(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    contacts = [{
        "id": "c1", "name": "Jane", "email": "jane@acme.com", "company": "Acme",
        "title": "Recruiter", "last_contact": "2026-01-01",
        "next_followup": "2026-07-01", "notes": "intro call", "job_id": "j1",
    }]
    store.save(profile=None, jobs=[], applications=[], approvals=[], plan=None,
               hunt_status="idle", contacts=contacts)
    snap = store.load()
    assert snap["contacts"] == contacts


def test_contacts_default_empty(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    store.save(profile=None, jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle")
    snap = store.load()
    assert snap["contacts"] == []


def test_state_restore_round_trips_contacts(tmp_path):
    store = DashboardStore(tmp_path / "t.db")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state.contacts.append({
        "id": "c1", "name": "Jane", "email": "jane@acme.com", "company": "",
        "title": "", "last_contact": None, "next_followup": None,
        "notes": "", "job_id": None,
    })
    state.persist()

    restored = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    restored.restore()
    assert len(restored.contacts) == 1
    assert restored.contacts[0]["email"] == "jane@acme.com"
