"""Tests for the email-identity magic-link layer.

Covers ``EmailIdentityStore`` (token lifecycle, email→workspace mapping,
``db_url`` routing — mirrors ``test_public_store.py``'s style),
``send_magic_link_email`` (offline via injected ``send_fn``), and the
``/api/auth/*`` endpoints wired into ``create_app`` in multi-tenant mode.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jobhunt.dashboard.auth import EmailIdentityStore, send_magic_link_email

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, WorkspaceManager, create_app,
)
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _tmp_store(tmp_path) -> EmailIdentityStore:
    return EmailIdentityStore(tmp_path / "test_auth.db")


# ---------------------------------------------------------------------------
# EmailIdentityStore
# ---------------------------------------------------------------------------

def test_create_and_consume_token_succeeds(tmp_path):
    store = _tmp_store(tmp_path)
    token = store.create_token("ada@example.com", ws_id_hint="ws-1")
    payload = store.consume_token(token)
    assert payload == {"email": "ada@example.com", "ws_id_hint": "ws-1"}


def test_consume_token_twice_fails_second_time(tmp_path):
    store = _tmp_store(tmp_path)
    token = store.create_token("ada@example.com")
    assert store.consume_token(token) is not None
    assert store.consume_token(token) is None  # single-use — replay rejected


def test_consume_unknown_token_returns_none(tmp_path):
    store = _tmp_store(tmp_path)
    assert store.consume_token("does-not-exist") is None


def test_consume_expired_token_returns_none(tmp_path):
    store = _tmp_store(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, 0)
    token = store.create_token("ada@example.com", ttl_seconds=60, now=now)
    later = now + timedelta(seconds=61)
    assert store.consume_token(token, now=later) is None


def test_consume_token_just_before_expiry_succeeds(tmp_path):
    store = _tmp_store(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, 0)
    token = store.create_token("ada@example.com", ttl_seconds=900, now=now)
    almost = now + timedelta(seconds=899)
    assert store.consume_token(token, now=almost) is not None


def test_email_to_workspace_round_trip(tmp_path):
    store = _tmp_store(tmp_path)
    assert store.get_workspace_for_email("ada@example.com") is None
    store.link_email_to_workspace("ada@example.com", "ws-abc")
    assert store.get_workspace_for_email("ada@example.com") == "ws-abc"


def test_link_email_to_workspace_upserts(tmp_path):
    store = _tmp_store(tmp_path)
    store.link_email_to_workspace("ada@example.com", "ws-1")
    store.link_email_to_workspace("ada@example.com", "ws-2")
    assert store.get_workspace_for_email("ada@example.com") == "ws-2"


def test_store_db_url_routes_through_explicit_url(tmp_path):
    db_file = tmp_path / "explicit_auth.db"
    store = EmailIdentityStore(db_url=f"sqlite:///{db_file}")
    store.link_email_to_workspace("ada@example.com", "ws-1")
    assert store.get_workspace_for_email("ada@example.com") == "ws-1"
    assert db_file.exists()


def test_store_db_url_takes_precedence_over_db_path(tmp_path):
    unused_path = tmp_path / "should_not_be_created.db"
    used_path = tmp_path / "used_auth.db"
    store = EmailIdentityStore(db_path=unused_path, db_url=f"sqlite:///{used_path}")
    store.link_email_to_workspace("ada@example.com", "ws-1")
    assert used_path.exists()
    assert not unused_path.exists()


# ---------------------------------------------------------------------------
# send_magic_link_email
# ---------------------------------------------------------------------------

def test_send_magic_link_email_with_send_fn_includes_link():
    captured = {}

    def fake_send(from_addr, to_addr, body):
        captured["from_addr"] = from_addr
        captured["to_addr"] = to_addr
        captured["body"] = body

    ok = send_magic_link_email(
        "ada@example.com", "https://example.com/api/auth/verify?token=abc",
        send_fn=fake_send,
    )
    assert ok is True
    assert captured["to_addr"] == "ada@example.com"
    assert "https://example.com/api/auth/verify?token=abc" in captured["body"]


def test_send_magic_link_email_without_smtp_or_send_fn_returns_false(monkeypatch):
    monkeypatch.delenv("JOBHUNT_SMTP_HOST", raising=False)
    ok = send_magic_link_email("ada@example.com", "https://example.com/verify?token=x")
    assert ok is False


# ---------------------------------------------------------------------------
# /api/auth/* endpoints
# ---------------------------------------------------------------------------

def _workspace_app(tmp_path, monkeypatch, dev_mode=True):
    monkeypatch.setenv("JOBHUNT_AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.delenv("JOBHUNT_SMTP_HOST", raising=False)  # force dev-link fallback
    if dev_mode:
        monkeypatch.setenv("JOBHUNT_AUTH_DEV_MODE", "1")
    else:
        monkeypatch.delenv("JOBHUNT_AUTH_DEV_MODE", raising=False)
    manager = WorkspaceManager(base_dir=tmp_path / "workspaces", cap=200)
    app = create_app(workspace_factory=manager.get)
    return manager, app


def test_request_link_returns_dev_link_when_smtp_unset_and_dev_mode_on(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch, dev_mode=True)
    client = TestClient(app)
    r = client.post("/api/auth/request-link", json={"email": "ada@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is False
    assert "dev_link" in body
    assert "/api/auth/verify?token=" in body["dev_link"]


def test_request_link_no_dev_link_when_dev_mode_off(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch, dev_mode=False)
    client = TestClient(app)
    r = client.post("/api/auth/request-link", json={"email": "ada@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is False
    assert "dev_link" not in body


def test_request_link_always_200_for_garbage_email(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post("/api/auth/request-link", json={"email": "not-an-email"})
    assert r.status_code == 200
    assert r.json()["sent"] is False


def _extract_token(dev_link: str) -> str:
    return dev_link.split("token=", 1)[1]


def test_verify_sets_cookie_and_links_email(tmp_path, monkeypatch):
    manager, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)

    r = client.post("/api/auth/request-link", json={"email": "ada@example.com"})
    token = _extract_token(r.json()["dev_link"])

    r2 = client.get(f"/api/auth/verify?token={token}")
    assert r2.status_code == 200
    assert r2.json() == {"verified": True, "email": "ada@example.com"}
    ws_id = client.cookies.get("jh_ws")
    assert ws_id is not None

    state = manager.get(ws_id)
    assert state.linked_email == "ada@example.com"

    r3 = client.get("/api/auth/status")
    assert r3.json() == {"linked_email": "ada@example.com"}


def test_verify_invalid_token_returns_400(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.get("/api/auth/verify?token=does-not-exist")
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid or expired link"


def test_verify_token_reused_fails(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post("/api/auth/request-link", json={"email": "ada@example.com"})
    token = _extract_token(r.json()["dev_link"])
    assert client.get(f"/api/auth/verify?token={token}").status_code == 200
    r2 = client.get(f"/api/auth/verify?token={token}")
    assert r2.status_code == 400


def test_verify_preserves_in_progress_workspace_on_first_link(tmp_path, monkeypatch):
    """First-ever link for an email should keep the requester's CURRENT
    workspace (their in-progress data), via ws_id_hint, rather than minting
    a brand-new empty one."""
    manager, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)

    # Establish a workspace with in-progress data before linking an email.
    client.get("/")
    original_ws = client.cookies.get("jh_ws")
    state = manager.get(original_ws)
    state.jobs = [{"job_id": "j-1", "title": "T", "company": "C", "status": "Saved"}]

    r = client.post("/api/auth/request-link", json={"email": "ada@example.com"})
    token = _extract_token(r.json()["dev_link"])
    client.get(f"/api/auth/verify?token={token}")

    assert client.cookies.get("jh_ws") == original_ws
    assert manager.get(original_ws).jobs != []


def test_second_device_verify_lands_on_same_workspace(tmp_path, monkeypatch):
    """Returning user, new device/browser: verifying the same email again
    from a fresh cookie jar must resolve to the SAME workspace id, recovering
    their existing data instead of minting a new empty workspace."""
    manager, app = _workspace_app(tmp_path, monkeypatch)
    client_a = TestClient(app)

    r1 = client_a.post("/api/auth/request-link", json={"email": "ada@example.com"})
    token1 = _extract_token(r1.json()["dev_link"])
    client_a.get(f"/api/auth/verify?token={token1}")
    ws_a = client_a.cookies.get("jh_ws")

    state_a = manager.get(ws_a)
    state_a.jobs = [{"job_id": "j-1", "title": "T", "company": "C", "status": "Saved"}]

    # Fresh client = fresh cookie jar = "new device".
    client_b = TestClient(app)
    r2 = client_b.post("/api/auth/request-link", json={"email": "ada@example.com"})
    token2 = _extract_token(r2.json()["dev_link"])
    r3 = client_b.get(f"/api/auth/verify?token={token2}")
    assert r3.status_code == 200

    ws_b = client_b.cookies.get("jh_ws")
    assert ws_b == ws_a
    assert manager.get(ws_b).jobs == state_a.jobs


def test_verify_single_tenant_mode_returns_400():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/auth/verify?token=anything")
    assert r.status_code == 400
    assert r.json()["detail"] == "not applicable in single-tenant mode"


def test_auth_status_defaults_to_none_for_fresh_workspace(tmp_path, monkeypatch):
    _, app = _workspace_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.get("/api/auth/status")
    assert r.json() == {"linked_email": None}
