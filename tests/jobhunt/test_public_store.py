"""Tests for the public résumé page SQLite store.

Mirrors ``test_persistence.py``'s coverage style for ``DashboardStore``.
"""

from __future__ import annotations

from jobhunt.public_store import PublicProfileStore


def _tmp_store(tmp_path) -> PublicProfileStore:
    return PublicProfileStore(tmp_path / "test_public.db")


def test_get_returns_none_for_unknown_handle(tmp_path):
    store = _tmp_store(tmp_path)
    assert store.get("nope") is None


def test_publish_then_get_round_trip(tmp_path):
    store = _tmp_store(tmp_path)
    draft = {"name": "Ada Lovelace", "summary": "Backend engineer."}
    store.publish("ada-lovelace", draft)
    assert store.get("ada-lovelace") == draft


def test_publish_upserts_existing_handle(tmp_path):
    store = _tmp_store(tmp_path)
    store.publish("ada", {"summary": "v1"})
    store.publish("ada", {"summary": "v2"})
    assert store.get("ada") == {"summary": "v2"}


def test_store_db_url_routes_through_explicit_url(tmp_path):
    """``db_url`` should be used verbatim instead of deriving from ``db_path``."""
    db_file = tmp_path / "explicit_public.db"
    store = PublicProfileStore(db_url=f"sqlite:///{db_file}")
    assert store.get("ada") is None
    store.publish("ada", {"summary": "hi"})
    assert store.get("ada") == {"summary": "hi"}
    assert db_file.exists()


def test_store_db_url_takes_precedence_over_db_path(tmp_path):
    """When both are given, ``db_url`` wins and ``db_path`` is not touched."""
    unused_path = tmp_path / "should_not_be_created.db"
    used_path = tmp_path / "used_public.db"
    store = PublicProfileStore(db_path=unused_path, db_url=f"sqlite:///{used_path}")
    store.publish("ada", {"summary": "hi"})
    assert used_path.exists()
    assert not unused_path.exists()
