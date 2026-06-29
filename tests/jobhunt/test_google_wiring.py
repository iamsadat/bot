"""Tests for Gmail send + Calendar + env factories + endpoint gating."""

from __future__ import annotations

import base64

import pytest

from jobhunt.integrations.gcal import GoogleCalendarClient
from jobhunt.integrations.gmail import GmailSender
from jobhunt.integrations.google_auth import FakeTransport, StaticTokenProvider

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402

_GOOGLE_ENV = ("JOBHUNT_GOOGLE_CLIENT_ID", "JOBHUNT_GOOGLE_CLIENT_SECRET",
               "JOBHUNT_GOOGLE_REFRESH_TOKEN")


def test_gmail_sender_posts_base64_message():
    tr = FakeTransport({("POST", "messages/send"): (200, {"id": "MSG1"})})
    sender = GmailSender(StaticTokenProvider("tok"), tr)
    mid = sender.send("recruiter@acme.com", "Hi", "Thanks for your time")
    assert mid == "MSG1"
    sent = tr.calls[-1]
    raw = base64.urlsafe_b64decode(sent["body"]["raw"] + "==").decode()
    assert "To: recruiter@acme.com" in raw and "Thanks for your time" in raw


def test_calendar_create_event():
    tr = FakeTransport({("POST", "calendar/v3"):
            (200, {"id": "EV1", "summary": "Interview", "htmlLink": "http://cal/EV1",
                   "start": {"dateTime": "2026-07-01T10:00:00Z"},
                   "end": {"dateTime": "2026-07-01T10:30:00Z"}})})
    cal = GoogleCalendarClient(StaticTokenProvider("tok"), tr)
    ev = cal.create_event("Interview", "2026-07-01T10:00:00Z", "2026-07-01T10:30:00Z")
    assert ev.id == "EV1" and ev.html_link.endswith("EV1")


def test_factories_none_without_env(monkeypatch):
    for k in _GOOGLE_ENV:
        monkeypatch.delenv(k, raising=False)
    from jobhunt.integrations.google_factory import (
        build_calendar_from_env, build_gmail_sender_from_env, google_configured,
    )
    assert build_gmail_sender_from_env() is None
    assert build_calendar_from_env() is None
    assert google_configured() is False


def test_factories_build_with_env(monkeypatch):
    monkeypatch.setenv("JOBHUNT_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("JOBHUNT_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("JOBHUNT_GOOGLE_REFRESH_TOKEN", "refresh")
    from jobhunt.integrations.google_factory import (
        build_gmail_source_from_env, google_configured,
    )
    assert google_configured() is True
    src = build_gmail_source_from_env()
    assert src is not None and src.name == "gmail"


def test_build_inbox_prefers_gmail(monkeypatch):
    monkeypatch.setenv("JOBHUNT_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("JOBHUNT_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("JOBHUNT_GOOGLE_REFRESH_TOKEN", "refresh")
    from jobhunt.dashboard.inbox_sync import build_inbox_from_env
    src = build_inbox_from_env()
    assert getattr(src, "name", "") == "gmail"


# ----- endpoint gating (unconfigured) --------------------------------------

def _client(monkeypatch):
    for k in _GOOGLE_ENV:
        monkeypatch.delenv(k, raising=False)
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return st, TestClient(create_app(st))


def test_email_and_calendar_gated_off(monkeypatch):
    _, c = _client(monkeypatch)
    s = c.get("/api/google/status").json()
    assert s["gmail_send"] is False and s["calendar"] is False
    assert c.post("/api/email/send", json={"to": "x@y.com", "body": "hi"}).status_code == 400
    assert c.post("/api/calendar/hold",
                  json={"summary": "x", "start": "a", "end": "b"}).status_code == 400
