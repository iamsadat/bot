"""Tests for relevance maximization: synonym taxonomy, blended discovery
relevance, and synonym-aware résumé keyword coverage."""

from __future__ import annotations

from jobhunt.agents.discovery import relevance
from jobhunt.agents.resume import (
    ResumeArchitectAgent,
    ResumeInputs,
    _best_keywords,
)
from jobhunt.models import JobPosting, UserProfile
from jobhunt.skills_taxonomy import expand_term, expand_terms
from jobhunt.trace import ThoughtBus, TraceStore


def _profile(skills):
    return UserProfile(
        user_id="u", name="Ada", email="ada@example.com",
        target_roles=["Backend Engineer"], locations=["Remote"], skills=skills,
    )


def _posting(jd, title="Backend Engineer"):
    return JobPosting(
        job_id="j1", source="g", source_id="1", url="https://x/j1",
        title=title, company="Acme", location="Remote", jd_text=jd, remote=True,
    )


# ── synonym taxonomy ─────────────────────────────────────────────────────────

def test_expand_term_includes_known_synonyms():
    assert "kubernetes" in expand_term("k8s")
    assert "k8s" in expand_term("kubernetes")
    assert "golang" in expand_term("go")


def test_expand_term_unknown_returns_self():
    assert expand_term("rustlang") == {"rustlang"}


def test_expand_terms_unions():
    out = expand_terms(["k8s", "py"])
    assert {"kubernetes", "python"} <= out


# ── blended discovery relevance ──────────────────────────────────────────────

def test_relevance_higher_for_related_job():
    prof = _profile(["python", "kubernetes", "postgresql"])
    related = _posting("Backend role using Python, Kubernetes and PostgreSQL at scale.")
    unrelated = _posting("Sales account executive owning quota and enterprise pipeline.",
                         title="Account Executive")
    assert relevance(related, prof) > relevance(unrelated, prof)


def test_relevance_credits_synonyms():
    """A JD that only says 'k8s' still scores for a 'kubernetes' skill."""
    prof = _profile(["kubernetes"])
    jd_syn = _posting("We run everything on k8s in production.")
    jd_none = _posting("We sell enterprise insurance products to brokers.")
    assert relevance(jd_syn, prof) > relevance(jd_none, prof)


def test_relevance_in_unit_range():
    prof = _profile(["python"])
    score = relevance(_posting("Python backend engineering."), prof)
    assert 0.0 <= score <= 1.0


# ── synonym-aware résumé coverage ────────────────────────────────────────────

def test_best_keywords_drops_filler():
    kws = _best_keywords(
        "We need a comfortable, passionate engineer fluent in Python and Kubernetes.",
        limit=10,
    )
    assert "python" in kws and "kubernetes" in kws
    assert "comfortable" not in kws and "passionate" not in kws


def test_resume_coverage_credits_synonyms():
    prof = _profile(["kubernetes", "postgresql", "machine-learning"])
    # JD uses alias surface forms only.
    post = _posting("Need k8s, psql and pytorch experience.")
    agent = ResumeArchitectAgent(TraceStore(), ThoughtBus())
    doc = agent.run(ResumeInputs(profile=prof, postings=[post]), task_id="t").output[0]
    # Each alias maps back to a real skill — and the bullets stay evidence-backed.
    assert set(doc.matched_keywords) >= {"k8s", "psql", "pytorch"}
    assert all(b.get("evidence_id") for b in doc.bullets)
