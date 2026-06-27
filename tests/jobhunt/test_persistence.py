"""Tests for the dashboard SQLite persistence layer."""

from __future__ import annotations



from jobhunt.approval import ApprovalQueue
from jobhunt.dashboard.persistence import DashboardStore, restore_approval_queue
from jobhunt.models import UserProfile


def _tmp_db(tmp_path) -> DashboardStore:
    return DashboardStore(tmp_path / "test.db")


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u1", name="Ada", email="ada@x.com",
        target_roles=["backend"], locations=["Remote"],
        skills=["python"], weekly_target=10,
    )


def test_store_returns_none_when_empty(tmp_path):
    store = _tmp_db(tmp_path)
    assert store.load() is None


def test_store_saves_and_loads_profile(tmp_path):
    store = _tmp_db(tmp_path)
    p = _profile()
    store.save(
        profile=p, jobs=[], applications=[], approvals=[],
        plan=None, hunt_status="idle",
    )
    snap = store.load()
    assert snap is not None
    assert snap["profile"].name == "Ada"
    assert snap["profile"].email == "ada@x.com"
    assert snap["profile"].target_roles == ["backend"]
    assert snap["hunt_status"] == "idle"


def test_store_persists_jobs(tmp_path):
    store = _tmp_db(tmp_path)
    jobs = [
        {"job_id": "j1", "title": "Backend", "company": "Acme",
         "status": "Saved", "relevance_score": 0.8},
        {"job_id": "j2", "title": "Staff", "company": "Globex",
         "status": "Applied", "relevance_score": 0.7},
    ]
    store.save(profile=_profile(), jobs=jobs, applications=[],
               approvals=[], plan=None, hunt_status="complete")
    snap = store.load()
    assert len(snap["jobs"]) == 2
    assert snap["jobs"][1]["status"] == "Applied"
    assert snap["hunt_status"] == "complete"


def test_store_persists_approvals(tmp_path):
    store = _tmp_db(tmp_path)
    q = ApprovalQueue()
    req = q.submit(job_id="j1", document_id="d1", company="Acme", title="Backend")
    q.approve(req.request_id, reviewer="ada")

    store.save(profile=_profile(), jobs=[], applications=[],
               approvals=q.all(), plan=None, hunt_status="complete")
    snap = store.load()
    assert len(snap["approvals"]) == 1
    assert snap["approvals"][0]["state"] == "approved"
    assert snap["approvals"][0]["reviewer"] == "ada"


def test_store_persists_ats_config(tmp_path):
    store = _tmp_db(tmp_path)
    store.save(profile=_profile(), jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle",
               ats_config={"greenhouse_tokens": ["stripe"], "lever_slugs": []})
    snap = store.load()
    assert snap["ats_config"]["greenhouse_tokens"] == ["stripe"]


def test_store_upserts_single_row(tmp_path):
    store = _tmp_db(tmp_path)
    p = _profile()
    store.save(profile=p, jobs=[], applications=[], approvals=[], plan=None,
               hunt_status="idle")
    store.save(profile=p, jobs=[{"job_id": "j1", "status": "Saved"}],
               applications=[], approvals=[], plan=None, hunt_status="running")
    snap = store.load()
    assert len(snap["jobs"]) == 1
    assert snap["hunt_status"] == "running"


def test_store_survives_reopen(tmp_path):
    db_path = tmp_path / "test.db"
    store1 = DashboardStore(db_path)
    store1.save(profile=_profile(), jobs=[{"job_id": "j1"}],
                applications=[], approvals=[], plan=None, hunt_status="idle")
    del store1

    # New store instance, same file
    store2 = DashboardStore(db_path)
    snap = store2.load()
    assert snap is not None
    assert snap["profile"].name == "Ada"
    assert len(snap["jobs"]) == 1


def test_restore_approval_queue_round_trip(tmp_path):
    q1 = ApprovalQueue()
    a = q1.submit(job_id="j1", document_id="d1", company="A", title="T1")
    b = q1.submit(job_id="j2", document_id="d2", company="B", title="T2")
    q1.approve(a.request_id, reviewer="ada")
    q1.reject(b.request_id, notes="bad fit")

    snapshots = [r.to_dict() for r in q1.all()]
    q2 = ApprovalQueue()
    restore_approval_queue(q2, snapshots)

    restored = q2.all()
    assert len(restored) == 2
    by_id = {r.request_id: r for r in restored}
    assert by_id[a.request_id].state.value == "approved"
    assert by_id[a.request_id].reviewer == "ada"
    assert by_id[b.request_id].state.value == "rejected"
    assert by_id[b.request_id].notes == "bad fit"


def test_store_clear(tmp_path):
    store = _tmp_db(tmp_path)
    store.save(profile=_profile(), jobs=[], applications=[], approvals=[],
               plan=None, hunt_status="idle")
    store.clear()
    assert store.load() is None
