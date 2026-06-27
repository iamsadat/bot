"""Tests for the onboarding module (resume parsing + profile builder)."""

from __future__ import annotations


from jobhunt.onboarding import build_user_profile, parse_resume_text


SAMPLE_RESUME = """
Ada Lovelace
ada@example.com

Senior Backend Engineer at Globex (2019 – 2023)
  - Built distributed Python services on Kubernetes with Redis and PostgreSQL
  - Led migration to FastAPI, reducing p99 latency by 40%

Staff Engineer at Initech (2023 – present)
  - Introduced OpenTelemetry across microservices; integrated Prometheus + Grafana
  - Shipped LangGraph-based agent framework powering an internal AI assistant

Skills: Python, Go, TypeScript, PostgreSQL, Redis, Kafka, Kubernetes, Docker,
        Terraform, AWS, GCP, FastAPI, React
"""


def test_parse_extracts_known_skills():
    result = parse_resume_text(SAMPLE_RESUME)
    skills = set(result["skills"])
    assert "python" in skills
    assert "redis" in skills
    assert "kubernetes" in skills
    assert "fastapi" in skills
    assert "kafka" in skills


def test_parse_does_not_hallucinate():
    result = parse_resume_text("I enjoy cooking and gardening.")
    assert result["skills"] == []


def test_parse_experience_years():
    result = parse_resume_text(SAMPLE_RESUME)
    # 2019–2025 (or whatever present maps to) — at minimum 2023-2019 = 4
    assert result["experience_years"] is not None
    assert result["experience_years"] >= 4


def test_parse_inferred_titles():
    result = parse_resume_text(SAMPLE_RESUME)
    titles_lower = [t.lower() for t in result["inferred_titles"]]
    assert any("engineer" in t for t in titles_lower)


def test_parse_empty_text():
    result = parse_resume_text("")
    assert result["skills"] == []
    assert result["inferred_titles"] == []
    assert result["experience_years"] is None


def test_build_user_profile_minimal():
    form = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "target_roles": ["backend engineer"],
        "locations": ["Remote"],
    }
    profile = build_user_profile(form)
    assert profile.name == "Ada Lovelace"
    assert profile.email == "ada@example.com"
    assert profile.target_roles == ["backend engineer"]
    assert profile.remote_ok is True
    assert profile.min_salary is None
    assert profile.user_id  # non-empty hex


def test_build_user_profile_full():
    form = {
        "name": "  Ada Lovelace  ",
        "email": "  ADA@EXAMPLE.COM  ",
        "target_roles": ["staff engineer", "principal engineer"],
        "locations": ["San Francisco", "Remote"],
        "min_salary": 200_000,
        "remote_ok": False,
        "skills": ["python", "  go  ", ""],
        "culture_keywords": ["mission-driven"],
        "veto_companies": ["Fabrikam"],
        "weekly_target": 5,
    }
    profile = build_user_profile(form)
    assert profile.name == "Ada Lovelace"
    assert profile.email == "ada@example.com"
    assert "go" in profile.skills
    assert "" not in profile.skills
    assert profile.min_salary == 200_000
    assert profile.remote_ok is False
    assert profile.weekly_target == 5
    assert "Fabrikam" in profile.veto_companies


def test_build_strips_empty_chips():
    form = {
        "name": "X", "email": "x@x.com",
        "target_roles": ["", "  ", "engineer"],
        "locations": ["Remote", ""],
        "skills": ["", "python"],
    }
    profile = build_user_profile(form)
    assert "" not in profile.target_roles
    assert "  " not in profile.target_roles
    assert "engineer" in profile.target_roles
    assert "" not in profile.locations
    assert "" not in profile.skills
