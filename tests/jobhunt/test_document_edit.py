"""The owner can hand-edit a tailored résumé; downloads follow and AI never overwrites it."""

import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard import server  # noqa: E402
from jobhunt.dashboard.server import _start_polish, create_app  # noqa: E402
from test_resume_polish import FakeClient, _posting, _state_with_docs  # noqa: E402


def _client():
    state = _state_with_docs([_posting(1)])
    return state, TestClient(create_app(state))


def test_edit_saves_draft_and_rebuilds_text():
    state, client = _client()
    draft = state.documents["j1"]["draft"]
    sec = next(s for s in draft["sections"] if s.get("rows") and s["rows"][0].get("bullets"))
    old_id = sec["rows"][0]["bullets"][0]["evidence_id"]
    sec["rows"][0]["bullets"][0]["text"] = "Hand-written bullet about Spark."
    sec["rows"][0]["bullets"].append({"text": "A brand new bullet."})
    draft["summary"] = "My own summary."

    r = client.put("/api/documents/j1", json={"draft": draft})
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["edited"] is True
    assert "My own summary." in doc["resume_text"]
    assert "- Hand-written bullet about Spark." in doc["resume_text"]
    rows = next(s for s in doc["draft"]["sections"] if s["title"] == sec["title"])["rows"]
    assert rows[0]["bullets"][0]["evidence_id"] == old_id
    assert rows[0]["bullets"][-1]["evidence_id"] == "edited"
    assert doc["draft"]["candidate_name"] == "Ada Lovelace"
    # Downloads render from the stored draft, so they carry the edit.
    for fmt in ("txt", "html"):
        r = client.get(f"/api/documents/j1/download?format={fmt}")
        assert "Hand-written bullet about Spark." in r.text


def test_invalid_payload_is_400():
    _, client = _client()
    for bad in ({}, {"draft": "x"}, {"draft": {"summary": 5}},
                {"draft": {"sections": [{"rows": "nope"}]}},
                {"draft": {"summary": "x" * 5000}},
                {"draft": {"sections": [{"rows": [{"bullets": [{"text": "b"}] * 21}]}]}}):
        assert client.put("/api/documents/j1", json=bad).status_code == 400, bad


def test_unknown_job_is_404():
    _, client = _client()
    assert client.put("/api/documents/nope", json={"draft": {}}).status_code == 404


def _join_polish():
    for t in threading.enumerate():
        if t.name == "resume-polish":
            t.join(10)


def test_polish_skips_an_edited_resume(monkeypatch):
    monkeypatch.setattr("jobhunt.llm.factory.build_llm_client_from_env", lambda: FakeClient())
    state, client = _client()
    client.put("/api/documents/j1", json={"draft": {"summary": "Mine."}})
    assert _start_polish(state, [_posting(1)]) == 0

    # Edited while the rewrite was already queued: the edit still wins.
    state.documents["j1"]["edited"] = False
    with server._POLISH_LOCK:
        assert _start_polish(state, [_posting(1)]) == 1
        state.documents["j1"]["edited"] = True
    _join_polish()
    assert "Mine." in state.documents["j1"]["resume_text"]
    assert "ai_status" not in state.documents["j1"]
