"""Tests for the tailored, layout-aware resume builder."""

from __future__ import annotations

from dataclasses import asdict

from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_template import ResumeDraft, build_tailored_resume


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u1", name="Ada Lovelace", email="ada@x.com",
        phone="555-1212", target_roles=["backend"], locations=["Remote"],
        skills=["python", "kubernetes", "postgresql", "redis"],
        experiences=[{
            "title": "Senior Backend Engineer", "company": "Globex",
            "location": "Remote", "start": "2019", "end": "2023",
            "bullets": [
                "Maintained an internal CRUD admin tool in PHP.",
                "Built distributed Python services on Kubernetes with Redis caching.",
            ],
        }],
        projects=[{
            "name": "JobHunt", "link": "github.com/ada/jobhunt",
            "bullets": ["Multi-agent platform in Python with PostgreSQL."],
        }],
        education=[{"school": "MIT", "degree": "BSc Computer Science", "end": "2019"}],
        links={"github": "github.com/ada"},
    )


def _posting() -> JobPosting:
    return JobPosting(
        job_id="j1", source="greenhouse", source_id="1",
        url="https://boards.greenhouse.io/acme/jobs/1",
        title="Backend Engineer", company="Acme", location="Remote",
        jd_text=("We need a backend engineer strong in Python, Kubernetes, "
                 "Redis and PostgreSQL to build distributed services."),
    )


def test_builder_produces_structured_sections():
    d = build_tailored_resume(_profile(), _posting())
    kinds = {s.kind for s in d.sections}
    assert {"experience", "projects", "skills", "education"} <= kinds
    assert d.phone == "555-1212"
    assert d.location == "Remote"


def test_bullets_reordered_by_jd_relevance():
    d = build_tailored_resume(_profile(), _posting())
    exp = next(s for s in d.sections if s.kind == "experience")
    bullets = exp.rows[0]["bullets"]
    # The Kubernetes/Redis bullet is far more relevant than the PHP CRUD one.
    assert "Kubernetes" in bullets[0]["text"]


def test_every_bullet_has_evidence_id():
    d = build_tailored_resume(_profile(), _posting())
    bullets = d.all_bullets()
    assert bullets  # non-empty
    assert all(b.evidence_id for b in bullets)
    assert any(b.evidence_id.startswith("exp:") for b in bullets)
    assert any(b.evidence_id.startswith("proj:") for b in bullets)


def test_matched_keywords_cover_real_skills():
    d = build_tailored_resume(_profile(), _posting())
    assert "python" in d.matched_keywords
    assert "kubernetes" in d.matched_keywords


def test_missing_structured_data_degrades_gracefully():
    skills_only = UserProfile(
        user_id="u", name="X", email="x@x.com",
        target_roles=[], locations=[], skills=["python"],
    )
    d = build_tailored_resume(skills_only, _posting())
    # No experience/projects rows, but skills section still present, no crash.
    assert not any(s.kind in ("experience", "projects") and s.rows for s in d.sections)
    assert any(s.kind == "skills" for s in d.sections)


def test_llm_failure_falls_back_to_original_text():
    def boom(action, payload):
        raise RuntimeError("llm down")

    d = build_tailored_resume(_profile(), _posting(), llm=boom)
    exp = next(s for s in d.sections if s.kind == "experience")
    texts = [b["text"] for b in exp.rows[0]["bullets"]]
    assert any("Kubernetes" in t for t in texts)  # verbatim source preserved


def test_draft_round_trips_through_asdict():
    d = build_tailored_resume(_profile(), _posting())
    restored = ResumeDraft.from_dict(asdict(d))
    assert restored.candidate_name == d.candidate_name
    assert len(restored.sections) == len(d.sections)
    assert restored.sections[0].rows == d.sections[0].rows


def test_to_text_keeps_bullets_and_headings():
    d = build_tailored_resume(_profile(), _posting())
    text = d.to_text()
    assert "EXPERIENCE" in text
    assert "- " in text  # bullet convention for the text fallback path
    assert "Ada Lovelace" in text
