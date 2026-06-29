"""Tests for outbound notifications (sinks + Notifier + env builder + wiring)."""

from __future__ import annotations

import pytest

from jobhunt.notify import (
    DiscordSink, EmailSink, NotificationEvent, Notifier, SlackSink, TelegramSink,
    WebhookSink, build_notifier_from_env,
)
from jobhunt.submitters.base import FakePoster

EV = NotificationEvent(kind="discovered", title="3 new matches", body="ready", url="http://x")


def test_slack_sink_posts_text():
    poster = FakePoster({"https://hooks.slack.test": (200, {})})
    assert SlackSink("https://hooks.slack.test", poster).send(EV) is True
    assert poster.calls[0]["body"]["text"].startswith("*3 new matches*")


def test_discord_and_webhook_payload_shapes():
    poster = FakePoster({"http://d": (204, {}), "http://w": (200, {})})
    assert DiscordSink("http://d", poster).send(EV)
    assert WebhookSink("http://w", poster).send(EV)
    assert poster.calls[0]["body"]["content"]  # discord uses "content"
    assert poster.calls[1]["body"]["kind"] == "discovered"  # webhook = structured


def test_telegram_targets_bot_api():
    poster = FakePoster()
    poster.add("https://api.telegram.org/botTOK/sendMessage", 200, {"ok": True})
    assert TelegramSink("TOK", "42", poster).send(EV)
    assert poster.calls[0]["body"]["chat_id"] == "42"


def test_failed_sink_does_not_raise_and_counts_zero():
    poster = FakePoster({"http://x": (500, {})})  # 5xx → not delivered
    n = Notifier([SlackSink("http://x", poster)])
    assert n.notify(EV) == 0


def test_email_sink_uses_injected_send_fn():
    sent = {}
    sink = EmailSink("smtp", 587, "me@x.com", "pw", "you@x.com",
                     send_fn=lambda frm, to, body: sent.update(to=to, body=body))
    assert sink.send(EV) is True
    assert sent["to"] == "you@x.com" and "3 new matches" in sent["body"]


def test_build_from_env_reads_channels(monkeypatch):
    for k in ("JOBHUNT_SLACK_WEBHOOK", "JOBHUNT_WEBHOOK_URLS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JOBHUNT_SLACK_WEBHOOK", "http://slack")
    monkeypatch.setenv("JOBHUNT_WEBHOOK_URLS", "http://a, http://b")
    n = build_notifier_from_env(poster=FakePoster())
    assert n is not None
    names = [s.name for s in n.sinks]
    assert names.count("webhook") == 2 and "slack" in names


def test_build_from_env_none_when_unconfigured(monkeypatch):
    for k in ("JOBHUNT_SLACK_WEBHOOK", "JOBHUNT_DISCORD_WEBHOOK",
              "JOBHUNT_TELEGRAM_BOT_TOKEN", "JOBHUNT_WEBHOOK_URLS", "JOBHUNT_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    assert build_notifier_from_env(poster=FakePoster()) is None


# ----- endpoint wiring -----------------------------------------------------

def test_notify_endpoints():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jobhunt.dashboard.server import DashboardState, create_app
    from jobhunt.trace import ThoughtBus, TraceStore

    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    c = TestClient(create_app(st))
    s = c.get("/api/notify/status").json()
    assert "configured" in s and "channels" in s
    # Unconfigured → test endpoint 400s rather than silently no-op.
    assert c.post("/api/notify/test").status_code == 400


def test_notify_helper_delivers_via_state_notifier():
    from jobhunt.dashboard.server import DashboardState, _notify
    from jobhunt.trace import ThoughtBus, TraceStore

    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    poster = FakePoster({"http://hook": (200, {})})
    st.notifier = Notifier([WebhookSink("http://hook", poster)])
    _notify(st, "applied", "Applied to 2 roles", "2/20 today")
    assert poster.calls and poster.calls[0]["body"]["title"] == "Applied to 2 roles"
