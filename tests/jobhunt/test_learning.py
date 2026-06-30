"""Tests for skill-gap learning paths."""

from __future__ import annotations

import pytest

from jobhunt.learning import compute_skill_gaps, resources_for
from jobhunt.trace import ThoughtBus, TraceStore


class _FakeState:
    """Minimal duck-typed stand-in for DashboardState (documents only)."""

    def __init__(self, documents: dict) -> None:
        self.documents = documents


def _seed_state() -> _FakeState:
    return _FakeState({
        "j1": {"job_id": "j1", "missing_keywords": ["kubernetes", "sql", "rust"]},
        "j2": {"job_id": "j2", "missing_keywords": ["k8s", "sql"]},
        "j3": {"job_id": "j3", "missing_keywords": ["kubernetes", "graphql"]},
    })


def test_compute_skill_gaps_ranks_by_count():
    state = _seed_state()
    gaps = compute_skill_gaps(state)
    assert gaps
    # kubernetes + k8s (synonyms) merge → count 3; sql → 2; rust/graphql → 1 each.
    top = gaps[0]
    assert top["skill"] == "kubernetes"
    assert top["count"] == 3

    sql_gap = next(g for g in gaps if g["skill"] == "sql")
    assert sql_gap["count"] == 2

    counts = [g["count"] for g in gaps]
    assert counts == sorted(counts, reverse=True)


def test_compute_skill_gaps_merges_synonyms_via_expand_term():
    state = _FakeState({
        "a": {"missing_keywords": ["kubernetes"]},
        "b": {"missing_keywords": ["k8s"]},
    })
    gaps = compute_skill_gaps(state)
    assert len(gaps) == 1
    assert gaps[0]["count"] == 2


def test_compute_skill_gaps_resource_mapping_known_skill():
    state = _seed_state()
    gaps = compute_skill_gaps(state)
    kube = next(g for g in gaps if g["skill"] == "kubernetes")
    assert kube["resources"]
    for r in kube["resources"]:
        assert r["title"] and r["url"].startswith("http")


def test_compute_skill_gaps_generic_fallback_for_unknown_skill():
    state = _FakeState({"a": {"missing_keywords": ["some-obscure-skill-xyz"]}})
    gaps = compute_skill_gaps(state)
    assert gaps[0]["skill"] == "some-obscure-skill-xyz"
    assert gaps[0]["resources"]
    assert gaps[0]["resources"][0]["url"].startswith("http")


def test_compute_skill_gaps_respects_top_limit():
    documents = {
        str(i): {"missing_keywords": [f"skill-{i}"]} for i in range(15)
    }
    state = _FakeState(documents)
    gaps = compute_skill_gaps(state, top=5)
    assert len(gaps) == 5


def test_compute_skill_gaps_empty_documents():
    state = _FakeState({})
    assert compute_skill_gaps(state) == []


def test_resources_for_known_and_unknown_skill():
    known = resources_for("python")
    assert known
    assert all("title" in r and "url" in r for r in known)

    unknown = resources_for("totally-made-up-skill")
    assert unknown
    assert unknown[0]["url"].startswith("http")


# --------------------------------------------------------------------------- API

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402


def _client():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return state, TestClient(create_app(state))


def test_skills_gaps_endpoint_shape():
    state, client = _client()
    state.documents["j1"] = {
        "job_id": "j1", "missing_keywords": ["kubernetes", "docker"],
    }
    state.documents["j2"] = {
        "job_id": "j2", "missing_keywords": ["docker"],
    }
    r = client.get("/api/skills/gaps")
    assert r.status_code == 200
    data = r.json()
    assert "gaps" in data
    assert data["gaps"]
    top = data["gaps"][0]
    assert top["skill"] == "docker"
    assert top["count"] == 2
    assert top["resources"]


def test_skills_gaps_endpoint_empty_state():
    _, client = _client()
    r = client.get("/api/skills/gaps")
    assert r.status_code == 200
    assert r.json() == {"gaps": []}
