"""Tests for recruiter-email auto-status: classify mail → advance matched jobs."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.inbox_sync import _match_job, sync_inbox  # noqa: E402
from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.inbox.sources import FakeInboxSource, InboxMessage  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _state_with_job(company="Acme Robotics", status="Applied"):
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    st.jobs = [{"job_id": "j1", "company": company, "title": "Backend",
                "status": status, "events": []}]
    return st


def _msg(subject, body, company="Acme Robotics", sender="recruiter@acmerobotics.com"):
    return InboxMessage(message_id="1", subject=subject, body=body,
                        sender=sender, received_at=1.0, company=company)


def test_match_job_by_company():
    st = _state_with_job()
    assert _match_job(st.jobs, _msg("hi", "x"))["job_id"] == "j1"


def test_match_job_by_sender_domain():
    st = _state_with_job(company="Stripe")
    m = _msg("hi", "x", company="", sender="talent@stripe.com")
    assert _match_job(st.jobs, m)["job_id"] == "j1"


def test_interview_email_advances_status_and_sets_next_action():
    st = _state_with_job(status="Applied")
    m = _msg("Interview invitation",
             "Let's schedule a call: https://zoom.us/j/123")
    res = sync_inbox(st, FakeInboxSource([m]))
    assert res == {"ok": True, "checked": 1, "updates": 1}
    assert st.jobs[0]["status"] == "Interview"
    assert "zoom.us" in st.jobs[0]["next_action"]
    assert st.jobs[0]["events"][-1]["stage"] == "Interview"


def test_rejection_email_closes_even_from_later_stage():
    st = _state_with_job(status="Interview")
    m = _msg("Update on your application",
             "Unfortunately we have decided to move forward with other candidates.")
    sync_inbox(st, FakeInboxSource([m]))
    assert st.jobs[0]["status"] == "Closed"


def test_does_not_regress_status():
    st = _state_with_job(status="Offer")
    m = _msg("Interview", "schedule a call")  # interview < offer
    sync_inbox(st, FakeInboxSource([m]))
    assert st.jobs[0]["status"] == "Offer"  # unchanged


def test_unmatched_company_is_ignored():
    st = _state_with_job(company="Acme")
    m = _msg("Offer!", "We are excited to extend an offer", company="Globex",
             sender="hr@globex.com")
    res = sync_inbox(st, FakeInboxSource([m]))
    assert res["updates"] == 0 and st.jobs[0]["status"] == "Applied"


# ── endpoints ────────────────────────────────────────────────────────────────

def test_status_reports_inbox_disconnected_by_default(monkeypatch):
    for k in ("JOBHUNT_IMAP_HOST", "JOBHUNT_IMAP_USER", "JOBHUNT_IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(st))
    assert client.get("/api/status").json()["inbox_connected"] is False


def test_inbox_sync_400_when_unconfigured(monkeypatch):
    for k in ("JOBHUNT_IMAP_HOST", "JOBHUNT_IMAP_USER", "JOBHUNT_IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(st))
    assert client.post("/api/inbox/sync", json={}).status_code == 400
