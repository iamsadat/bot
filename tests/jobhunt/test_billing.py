"""Tests for billing rails: Stripe Checkout + webhook signature verification.

No pricing decided yet — this is pure scaffolding, off by default. Covers
``verify_webhook_signature`` (stdlib HMAC scheme, deterministic via an
injectable ``now``), ``StripeClient.create_checkout_session`` (offline via
the existing ``FakePoster`` test double from ``jobhunt/submitters/base.py``),
``build_stripe_client_from_env``, and the ``/api/billing/*`` endpoints wired
into ``create_app``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from jobhunt.dashboard.billing import (
    StripeClient, build_stripe_client_from_env, verify_webhook_signature,
)
from jobhunt.submitters.base import FakePoster

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, WorkspaceManager, create_app,
)
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _sign(payload: bytes, secret: str, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# verify_webhook_signature
# ---------------------------------------------------------------------------

def test_valid_signature_accepted():
    payload = b'{"type": "checkout.session.completed"}'
    secret = "whsec_test"
    now = 1_700_000_000
    sig = _sign(payload, secret, now)
    header = f"t={now},v1={sig}"
    assert verify_webhook_signature(payload, header, secret, now=now) is True


def test_tampered_payload_rejected():
    payload = b'{"type": "checkout.session.completed"}'
    secret = "whsec_test"
    now = 1_700_000_000
    sig = _sign(payload, secret, now)
    header = f"t={now},v1={sig}"
    tampered = b'{"type": "checkout.session.completed", "evil": true}'
    assert verify_webhook_signature(tampered, header, secret, now=now) is False


def test_wrong_secret_rejected():
    payload = b'{"type": "x"}'
    now = 1_700_000_000
    sig = _sign(payload, "whsec_real", now)
    header = f"t={now},v1={sig}"
    assert verify_webhook_signature(payload, header, "whsec_wrong", now=now) is False


def test_stale_timestamp_rejected():
    payload = b'{"type": "x"}'
    secret = "whsec_test"
    now = 1_700_000_000
    sig = _sign(payload, secret, now)
    header = f"t={now},v1={sig}"
    later = now + 301  # > 5 minute default tolerance
    assert verify_webhook_signature(payload, header, secret, now=later) is False


def test_timestamp_within_tolerance_accepted():
    payload = b'{"type": "x"}'
    secret = "whsec_test"
    now = 1_700_000_000
    sig = _sign(payload, secret, now)
    header = f"t={now},v1={sig}"
    later = now + 299
    assert verify_webhook_signature(payload, header, secret, now=later) is True


def test_custom_tolerance_overridable():
    payload = b'{"type": "x"}'
    secret = "whsec_test"
    now = 1_700_000_000
    sig = _sign(payload, secret, now)
    header = f"t={now},v1={sig}"
    later = now + 10
    assert verify_webhook_signature(
        payload, header, secret, now=later, tolerance_seconds=5,
    ) is False


def test_multiple_v1_entries_any_match_accepted():
    """Key-rotation: Stripe sends multiple v1= signatures; accept if any match."""
    payload = b'{"type": "x"}'
    secret = "whsec_new"
    now = 1_700_000_000
    bad_sig = "0" * 64
    good_sig = _sign(payload, secret, now)
    header = f"t={now},v1={bad_sig},v1={good_sig}"
    assert verify_webhook_signature(payload, header, secret, now=now) is True


def test_missing_header_rejected():
    assert verify_webhook_signature(b"{}", "", "whsec_test", now=1_700_000_000) is False


def test_missing_secret_rejected():
    header = "t=1700000000,v1=deadbeef"
    assert verify_webhook_signature(b"{}", header, "", now=1_700_000_000) is False


def test_malformed_header_rejected():
    assert verify_webhook_signature(
        b"{}", "garbage-header", "whsec_test", now=1_700_000_000,
    ) is False


# ---------------------------------------------------------------------------
# StripeClient.create_checkout_session
# ---------------------------------------------------------------------------

def test_create_checkout_session_sends_expected_request_and_parses_response():
    poster = FakePoster()
    poster.add(
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        {"id": "cs_test_123", "url": "https://checkout.stripe.com/pay/cs_test_123"},
    )
    client = StripeClient("sk_test_abc", poster)
    result = client.create_checkout_session(
        "ws-1", "ada@example.com", "price_123",
        success_url="https://example.com/dashboard?checkout=success",
        cancel_url="https://example.com/dashboard?checkout=cancelled",
    )
    assert result == {
        "id": "cs_test_123", "url": "https://checkout.stripe.com/pay/cs_test_123",
    }
    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert call["headers"]["Authorization"] == "Bearer sk_test_abc"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

    from urllib.parse import parse_qs
    body = call["body"]
    assert isinstance(body, bytes)
    form = parse_qs(body.decode())
    assert form["mode"] == ["subscription"]
    assert form["line_items[0][price]"] == ["price_123"]
    assert form["line_items[0][quantity]"] == ["1"]
    assert form["client_reference_id"] == ["ws-1"]
    assert form["customer_email"] == ["ada@example.com"]
    assert form["success_url"] == ["https://example.com/dashboard?checkout=success"]
    assert form["cancel_url"] == ["https://example.com/dashboard?checkout=cancelled"]


def test_create_checkout_session_omits_email_when_none():
    poster = FakePoster()
    poster.add(
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        {"id": "cs_1", "url": "https://checkout.stripe.com/pay/cs_1"},
    )
    client = StripeClient("sk_test_abc", poster)
    client.create_checkout_session(
        "ws-1", None, "price_123",
        success_url="https://example.com/s", cancel_url="https://example.com/c",
    )
    from urllib.parse import parse_qs
    form = parse_qs(poster.calls[0]["body"].decode())
    assert "customer_email" not in form


def test_create_checkout_session_raises_on_error_status():
    poster = FakePoster()
    poster.add(
        "https://api.stripe.com/v1/checkout/sessions",
        402,
        {"error": {"message": "bad request"}},
    )
    client = StripeClient("sk_test_abc", poster)
    with pytest.raises(RuntimeError):
        client.create_checkout_session(
            "ws-1", None, "price_123",
            success_url="https://example.com/s", cancel_url="https://example.com/c",
        )


# ---------------------------------------------------------------------------
# build_stripe_client_from_env
# ---------------------------------------------------------------------------

def test_build_stripe_client_from_env_none_when_unset(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert build_stripe_client_from_env() is None


def test_build_stripe_client_from_env_returns_client_when_set(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    client = build_stripe_client_from_env()
    assert isinstance(client, StripeClient)
    assert client._key == "sk_test_abc"


# ---------------------------------------------------------------------------
# /api/billing/* endpoints
# ---------------------------------------------------------------------------

def test_checkout_returns_400_when_not_configured(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))
    r = client.post("/api/billing/checkout", json={})
    assert r.status_code == 400


def test_checkout_returns_400_when_price_id_missing(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))
    r = client.post("/api/billing/checkout", json={})
    assert r.status_code == 400


def test_checkout_returns_url_when_configured(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_123")

    poster = FakePoster()
    poster.add(
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        {"id": "cs_1", "url": "https://checkout.stripe.com/pay/cs_1"},
    )

    import jobhunt.dashboard.billing as billing_mod
    real_builder = billing_mod.build_stripe_client_from_env
    monkeypatch.setattr(
        billing_mod, "build_stripe_client_from_env",
        lambda: real_builder(poster),
    )

    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))
    r = client.post("/api/billing/checkout", json={})
    assert r.status_code == 200
    assert r.json() == {"url": "https://checkout.stripe.com/pay/cs_1"}

    # client_reference_id falls back to the single-tenant placeholder.
    from urllib.parse import parse_qs
    form = parse_qs(poster.calls[0]["body"].decode())
    assert form["client_reference_id"] == ["single-tenant"]


def test_checkout_includes_linked_email(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_123")

    poster = FakePoster()
    poster.add(
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        {"id": "cs_1", "url": "https://checkout.stripe.com/pay/cs_1"},
    )

    import jobhunt.dashboard.billing as billing_mod
    real_builder = billing_mod.build_stripe_client_from_env
    monkeypatch.setattr(
        billing_mod, "build_stripe_client_from_env",
        lambda: real_builder(poster),
    )

    state = DashboardState(
        trace_store=TraceStore(), bus=ThoughtBus(), linked_email="ada@example.com",
    )
    client = TestClient(create_app(state))
    client.post("/api/billing/checkout", json={})
    from urllib.parse import parse_qs
    form = parse_qs(poster.calls[0]["body"].decode())
    assert form["customer_email"] == ["ada@example.com"]


def _signed_webhook_request(client, secret, event: dict, *, ts: int | None = None):
    # The endpoint verifies against the real wall clock (no injectable `now`
    # over HTTP), so default to "right now" unless a test wants a stale ts.
    ts = int(time.time()) if ts is None else ts
    payload = json.dumps(event).encode()
    sig = _sign(payload, secret, ts)
    header = f"t={ts},v1={sig}"
    return client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": header, "Content-Type": "application/json"},
    )


def test_webhook_checkout_completed_sets_plan_pro_single_tenant(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "single-tenant",
            "customer_email": "ada@example.com",
        }},
    }
    r = _signed_webhook_request(client, "whsec_test", event)
    assert r.status_code == 200
    assert r.json() == {"received": True}
    assert state.billing_plan == "pro"

    status = client.get("/api/billing/status").json()
    assert status["plan"] == "pro"


def test_webhook_checkout_completed_sets_plan_pro_multi_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    manager = WorkspaceManager(base_dir=tmp_path / "workspaces", cap=200)
    app = create_app(workspace_factory=manager.get)
    client = TestClient(app)

    # Establish a workspace so the test ws_id resolves to a real one.
    client.get("/")
    ws_id = client.cookies.get("jh_ws")

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": ws_id,
            "customer_email": "ada@example.com",
        }},
    }
    r = _signed_webhook_request(client, "whsec_test", event)
    assert r.status_code == 200
    assert manager.get(ws_id).billing_plan == "pro"


def test_webhook_ignores_unrelated_event_types(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))

    event = {"type": "invoice.paid", "data": {"object": {}}}
    r = _signed_webhook_request(client, "whsec_test", event)
    assert r.status_code == 200
    assert r.json() == {"received": True}
    assert state.billing_plan == "free"


def test_webhook_bad_signature_returns_400(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))

    payload = json.dumps({"type": "checkout.session.completed"}).encode()
    r = client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1700000000,v1=deadbeef",
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert state.billing_plan == "free"


def test_webhook_unconfigured_secret_returns_400(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))

    payload = json.dumps({"type": "checkout.session.completed"}).encode()
    r = client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1700000000,v1=deadbeef",
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/billing/status
# ---------------------------------------------------------------------------

def test_billing_status_reflects_plan_and_configured(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))

    r = client.get("/api/billing/status")
    assert r.json() == {"plan": "free", "billing_configured": False}

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    r2 = client.get("/api/billing/status")
    assert r2.json() == {"plan": "free", "billing_configured": True}

    state.billing_plan = "pro"
    r3 = client.get("/api/billing/status")
    assert r3.json()["plan"] == "pro"


def test_plan_persists_across_restore(tmp_path):
    from jobhunt.dashboard.persistence import DashboardStore

    store = DashboardStore(tmp_path / "jobhunt_test.db")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state.billing_plan = "pro"
    state.persist()

    state2 = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state2.restore()
    assert state2.billing_plan == "pro"


def test_plan_defaults_to_free_on_fresh_store(tmp_path):
    from jobhunt.dashboard.persistence import DashboardStore

    store = DashboardStore(tmp_path / "jobhunt_test2.db")
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state.persist()  # writes default "free"

    state2 = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state2.restore()
    assert state2.billing_plan == "free"
