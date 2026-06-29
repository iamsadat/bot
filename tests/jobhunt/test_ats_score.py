"""Tests for the free, no-auth ATS match-score tool.

``POST /api/tools/ats-score`` is a top-of-funnel growth tool: it must work
with zero stored state (no profile, no job, no auth) and run fully offline.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _client():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return TestClient(create_app(state))


_JD = (
    "We need a backend engineer fluent in Python and Kubernetes, with "
    "experience running PostgreSQL and Docker in production. Familiarity "
    "with GraphQL is a plus."
)


def test_matched_and_missing_keywords():
    client = _client()
    resume = "Built Python services backed by PostgreSQL, deployed via Docker."
    r = client.post(
        "/api/tools/ats-score", json={"resume_text": resume, "jd_text": _JD}
    )
    assert r.status_code == 200
    body = r.json()
    assert "python" in body["matched"]
    assert "postgresql" in body["matched"] or "postgres" in body["matched"]
    assert "docker" in body["matched"]
    # Kubernetes/GraphQL never appear in the resume → must be in missing.
    assert "kubernetes" in body["missing"]
    assert set(body["matched"]) & set(body["missing"]) == set()


def test_synonym_credit_k8s_matches_kubernetes():
    client = _client()
    # JD says "kubernetes"; résumé only says "k8s" — taxonomy synonym credit.
    resume = "Operated k8s clusters at scale; wrote Python tooling around them."
    r = client.post(
        "/api/tools/ats-score",
        json={"resume_text": resume, "jd_text": "Looking for a kubernetes expert."},
    )
    assert r.status_code == 200
    body = r.json()
    assert "kubernetes" in body["matched"]
    assert "kubernetes" not in body["missing"]


def test_score_math_matches_ratio():
    client = _client()
    resume = "Built Python services backed by PostgreSQL, deployed via Docker."
    r = client.post(
        "/api/tools/ats-score", json={"resume_text": resume, "jd_text": _JD}
    )
    body = r.json()
    expected = round(len(body["matched"]) / max(1, len(body["matched"]) + len(body["missing"])), 3)
    assert body["score"] == expected
    assert 0.0 <= body["score"] <= 1.0


def test_suggestions_are_first_eight_missing():
    client = _client()
    r = client.post(
        "/api/tools/ats-score", json={"resume_text": "", "jd_text": _JD}
    )
    body = r.json()
    assert body["suggestions"] == body["missing"][:8]
    assert len(body["suggestions"]) <= 8


def test_both_empty_inputs_422():
    client = _client()
    r = client.post(
        "/api/tools/ats-score", json={"resume_text": "  ", "jd_text": ""}
    )
    assert r.status_code == 422


def test_missing_fields_default_to_empty_and_422():
    client = _client()
    r = client.post("/api/tools/ats-score", json={})
    assert r.status_code == 422


def test_only_jd_text_is_allowed_zero_state():
    # Top-of-funnel: a JD with no résumé is valid input (everything "missing").
    client = _client()
    r = client.post(
        "/api/tools/ats-score", json={"resume_text": "", "jd_text": _JD}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] == []
    assert body["score"] == 0.0
