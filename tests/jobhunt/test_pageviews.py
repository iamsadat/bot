"""Tests for the privacy-friendly pageview counter (store + API endpoints).

Mirrors ``test_public_store.py``'s coverage style for ``PageviewStore``, and
``test_public_page.py``'s ``TestClient`` setup for the endpoint integration.
"""

from __future__ import annotations

import dataclasses

import pytest

from jobhunt.dashboard.pageviews import PageviewStore

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.models import JobPosting, UserProfile  # noqa: E402
from jobhunt.resume_template import build_tailored_resume  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _tmp_store(tmp_path) -> PageviewStore:
    return PageviewStore(tmp_path / "test_pageviews.db")


# --------------------------------------------------------------------- store

def test_empty_store_returns_zeroed_structure(tmp_path):
    store = _tmp_store(tmp_path)
    counts = store.counts()
    assert counts == {
        "landing": {"total": 0, "by_day": {}},
        "ats_tool": {"total": 0, "by_day": {}},
        "public_resume": {"total": 0, "by_day": {}, "top_refs": []},
    }


def test_record_then_counts_round_trip(tmp_path):
    store = _tmp_store(tmp_path)
    store.record("landing", ref=None, day="2026-06-30")
    store.record("landing", ref=None, day="2026-06-30")
    store.record("landing", ref=None, day="2026-06-29")
    store.record("ats_tool", ref=None, day="2026-06-30")

    counts = store.counts()
    assert counts["landing"]["total"] == 3
    assert counts["landing"]["by_day"] == {"2026-06-29": 1, "2026-06-30": 2}
    assert counts["ats_tool"]["total"] == 1
    assert counts["ats_tool"]["by_day"] == {"2026-06-30": 1}
    assert counts["public_resume"]["total"] == 0


def test_counts_scoped_to_single_surface(tmp_path):
    store = _tmp_store(tmp_path)
    store.record("landing", ref=None, day="2026-06-30")
    store.record("ats_tool", ref=None, day="2026-06-30")

    counts = store.counts("landing")
    assert list(counts.keys()) == ["landing"]
    assert counts["landing"]["total"] == 1


def test_public_resume_top_refs_ranking(tmp_path):
    store = _tmp_store(tmp_path)
    for _ in range(3):
        store.record("public_resume", ref="ada-1a2b3c", day="2026-06-30")
    for _ in range(5):
        store.record("public_resume", ref="grace-9z8y7x", day="2026-06-30")
    store.record("public_resume", ref="linus-aaa111", day="2026-06-30")

    top_refs = store.counts("public_resume")["public_resume"]["top_refs"]
    assert top_refs[0] == {"ref": "grace-9z8y7x", "count": 5}
    assert top_refs[1] == {"ref": "ada-1a2b3c", "count": 3}
    assert top_refs[2] == {"ref": "linus-aaa111", "count": 1}


def test_by_day_window_caps_at_30_distinct_days(tmp_path):
    store = _tmp_store(tmp_path)
    for day_num in range(1, 41):  # 40 distinct days
        store.record("landing", ref=None, day=f"2026-01-{day_num:02d}" if day_num <= 31
                      else f"2026-02-{day_num - 31:02d}")

    by_day = store.counts("landing")["landing"]["by_day"]
    assert len(by_day) == 30
    # the most recent 30 days should be kept (the latest being Feb 9)
    assert "2026-02-09" in by_day
    assert "2026-01-01" not in by_day


def test_db_url_routes_through_explicit_url(tmp_path):
    """``db_url`` should be used verbatim instead of deriving from ``db_path``."""
    db_file = tmp_path / "explicit_pageviews.db"
    store = PageviewStore(db_url=f"sqlite:///{db_file}")
    store.record("landing", ref=None, day="2026-06-30")
    assert db_file.exists()
    assert store.counts()["landing"]["total"] == 1


def test_db_url_takes_precedence_over_db_path(tmp_path):
    """When both are given, ``db_url`` wins and ``db_path`` is not touched."""
    unused_path = tmp_path / "should_not_be_created.db"
    used_path = tmp_path / "used_pageviews.db"
    store = PageviewStore(db_path=unused_path, db_url=f"sqlite:///{used_path}")
    store.record("landing", ref=None, day="2026-06-30")
    assert used_path.exists()
    assert not unused_path.exists()


# ----------------------------------------------------------------- endpoints

def _draft_dict() -> dict:
    profile = UserProfile(
        user_id="u1", name="Ada Lovelace", email="ada@x.com", phone="555",
        target_roles=["backend"], locations=["Remote"],
        skills=["python", "kubernetes"],
        experiences=[{
            "title": "Senior Backend Engineer", "company": "Acme",
            "location": "Remote", "start": "Jan 2019", "end": "Present",
            "bullets": ["Built distributed Python services on Kubernetes."],
        }],
    )
    posting = JobPosting(
        job_id="j1", source="gh", source_id="1", url="https://x/y",
        title="Backend Engineer", company="Acme", location="Remote",
        jd_text="Python Kubernetes backend services.",
    )
    draft = build_tailored_resume(profile, posting)
    return dataclasses.asdict(draft)


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_PAGEVIEWS_DB_PATH", str(tmp_path / "pageviews.db"))
    monkeypatch.setenv("JOBHUNT_PUBLIC_DB_PATH", str(tmp_path / "public.db"))
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.documents["j1"] = {
        "company": "Acme", "title": "Backend Engineer", "draft": _draft_dict(),
    }
    return state, TestClient(create_app(state))


@pytest.mark.parametrize("surface", ["landing", "ats_tool", "public_resume"])
def test_post_pageview_valid_surfaces(tmp_path, monkeypatch, surface):
    _, client = _client(tmp_path, monkeypatch)
    r = client.post("/api/pageview", json={"surface": surface, "ref": None})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_post_pageview_invalid_surface_400(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.post("/api/pageview", json={"surface": "bogus", "ref": None})
    assert r.status_code == 400


def test_get_pageview_stats_reflects_recorded_views(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    client.post("/api/pageview", json={"surface": "landing", "ref": None})
    client.post("/api/pageview", json={"surface": "landing", "ref": None})
    client.post("/api/pageview", json={"surface": "ats_tool", "ref": None})

    stats = client.get("/api/pageview/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["landing"]["total"] == 2
    assert body["ats_tool"]["total"] == 1
    assert body["public_resume"]["total"] == 0


def test_public_resume_page_increments_pageview_count(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    pub = client.post("/api/publish", json={"job_id": "j1"})
    handle = pub.json()["handle"]

    # No view recorded yet.
    stats = client.get("/api/pageview/stats").json()
    assert stats["public_resume"]["total"] == 0

    page = client.get(f"/p/{handle}")
    assert page.status_code == 200

    stats = client.get("/api/pageview/stats").json()
    assert stats["public_resume"]["total"] == 1
    assert stats["public_resume"]["top_refs"] == [{"ref": handle, "count": 1}]

    # A second view increments further.
    client.get(f"/p/{handle}")
    stats = client.get("/api/pageview/stats").json()
    assert stats["public_resume"]["total"] == 2
    assert stats["public_resume"]["top_refs"] == [{"ref": handle, "count": 2}]


def test_public_resume_404_does_not_record_a_view(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.get("/p/does-not-exist")
    assert r.status_code == 404
    stats = client.get("/api/pageview/stats").json()
    assert stats["public_resume"]["total"] == 0
