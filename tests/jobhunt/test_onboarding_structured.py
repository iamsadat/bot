"""Tests for additive structured-section parsing in onboarding.

The primary keys (skills/inferred_titles/experience_years) must never be
dropped, and section-heading-based extraction should recover experience,
education, and project entries for the resume builder to prefill.
"""

from __future__ import annotations

from jobhunt.onboarding import build_user_profile, parse_resume_text

STRUCTURED_RESUME = """
Ada Lovelace
ada@example.com | github.com/ada | https://ada.dev

Experience
Senior Backend Engineer, Globex, Remote    Jan 2019 - 2023
  - Built distributed Python services on Kubernetes with Redis and PostgreSQL
  - Led migration to FastAPI, reducing p99 latency by 40%
Staff Engineer, Initech, NYC    2023 - Present
  - Introduced OpenTelemetry across microservices

Projects
JobHunt, https://github.com/ada/jobhunt
  - Multi-agent job application platform in Python

Education
MIT, BSc Computer Science    2019

Skills
Python, Go, PostgreSQL, Redis, Kubernetes
"""


def test_primary_keys_survive_structured_parse():
    r = parse_resume_text(STRUCTURED_RESUME)
    assert "python" in r["skills"]
    assert r["inferred_titles"]  # non-empty
    assert r["experience_years"] is not None


def test_extracts_experience_entries_with_dates_and_bullets():
    r = parse_resume_text(STRUCTURED_RESUME)
    exps = r["experiences"]
    assert len(exps) >= 2
    first = exps[0]
    assert "Engineer" in first["title"]
    assert first["company"] == "Globex"
    assert "2019" in first["start"]
    assert any("Kubernetes" in b for b in first["bullets"])
    # "Present" end is recognised
    assert any(e["end"].lower() in ("present", "current", "now") for e in exps)


def test_extracts_education_and_projects_and_links():
    r = parse_resume_text(STRUCTURED_RESUME)
    assert any("MIT" in e["school"] for e in r["education"])
    assert any("jobhunt" in p["name"].lower() for p in r["projects"])
    proj = next(p for p in r["projects"] if "jobhunt" in p["name"].lower())
    assert "github.com/ada/jobhunt" in proj["link"]
    assert r["links"].get("github", "").endswith("ada")
    assert r["links"].get("website") == "https://ada.dev"


def test_unstructured_text_yields_empty_sections_not_error():
    r = parse_resume_text("I like cooking and gardening.")
    assert r["experiences"] == []
    assert r["education"] == []
    assert r["projects"] == []
    assert r["skills"] == []


def test_build_user_profile_passes_structured_sections():
    form = {
        "name": "Ada", "email": "ada@x.com",
        "target_roles": ["backend"], "locations": ["Remote"],
        "experiences": [{"title": "Eng", "company": "Globex", "bullets": ["x"]}],
        "education": [{"school": "MIT", "degree": "BSc"}],
        "projects": [{"name": "JobHunt", "link": "gh/ada"}],
        "links": {"github": "github.com/ada"},
        "auto_apply": True, "daily_apply_cap": 5, "relevance_floor": 0.3,
    }
    p = build_user_profile(form)
    assert p.education[0]["school"] == "MIT"
    assert p.projects[0]["name"] == "JobHunt"
    assert p.links["github"] == "github.com/ada"
    assert p.auto_apply is True
    assert p.daily_apply_cap == 5
    assert p.relevance_floor == 0.3
