"""Tests for the production-grade JD parser (Phase 2).

Validates HTML stripping, TF-IDF ranking, ATS categorisation, and section
splitting. All offline — no network, no LLM calls.
"""

from __future__ import annotations

import pytest

from jobhunt.jd_parser import (
    html_to_text,
    tfidf_keywords,
    frequency_keywords,
    categorise,
    split_sections,
    parse_jd,
)


def test_html_to_text_strips_tags_and_preserves_paragraphs():
    html = "<p>Backend Engineer.</p><script>alert('x')</script><div>Python required.</div>"
    out = html_to_text(html)
    assert "alert" not in out  # script removed
    assert "Backend Engineer" in out
    assert "Python required" in out


def test_html_to_text_passthrough_for_plain_text():
    text = "Already plain. No tags here."
    assert html_to_text(text) == text


def test_tfidf_lifts_distinctive_terms():
    jd = (
        "We need a Python engineer with deep Kubernetes and Redis experience. "
        "You will build distributed systems using LangGraph and pgvector."
    )
    ranked = tfidf_keywords(jd, limit=10)
    terms = [t for t, _ in ranked]
    # Distinctive techy terms should appear; common filler should not dominate.
    assert "kubernetes" in terms or "redis" in terms or "pgvector" in terms
    # Stopwords excluded.
    assert "the" not in terms
    assert "with" not in terms


def test_frequency_keywords_counts_correctly():
    jd = "python python python typescript typescript javascript"
    ranked = frequency_keywords(jd, limit=5)
    counts = dict(ranked)
    assert counts["python"] == 3
    assert counts["typescript"] == 2
    assert counts["javascript"] == 1


def test_categorise_separates_skills_and_quals_and_responsibilities():
    jd = (
        "Build and ship Python services on Kubernetes. "
        "5+ years of backend experience. BS in computer science."
    )
    keywords = ["python", "kubernetes", "build", "ship", "backend"]
    skills, quals, resps = categorise(jd, keywords)
    assert "python" in skills and "kubernetes" in skills
    assert any("years" in q for q in quals)
    assert any("bs in" in q for q in quals)
    assert "build" in resps and "ship" in resps


def test_split_sections_finds_responsibilities_and_requirements():
    jd = (
        "About us:\nWe make widgets.\n\n"
        "Responsibilities:\nBuild stuff.\nShip code.\n\n"
        "Requirements:\n5+ years Python.\nKubernetes."
    )
    secs = split_sections(jd)
    assert "responsibilities" in secs
    assert "build stuff" in secs["responsibilities"].lower()
    assert "requirements" in secs
    assert "python" in secs["requirements"].lower()


def test_parse_jd_end_to_end_with_html():
    html = (
        "<h2>Senior Backend Engineer</h2>"
        "<p>Build distributed Python services on Kubernetes.</p>"
        "<h3>Requirements</h3><ul>"
        "<li>5+ years of backend experience</li>"
        "<li>Strong Python, Redis, Postgres</li></ul>"
    )
    parsed = parse_jd(html, limit=15)
    assert "<" not in parsed.cleaned
    assert "python" in parsed.union_keywords
    assert "python" in parsed.skills
    assert any("years" in q for q in parsed.qualifications)


def test_parse_jd_empty_input_does_not_crash():
    parsed = parse_jd("", limit=10)
    assert parsed.cleaned == ""
    assert parsed.union_keywords == []
    assert parsed.skills == []
    assert parsed.qualifications == []


def test_union_keywords_combines_tfidf_and_frequency():
    # Repeated common word (high frequency, low TF-IDF) + rare techy word
    # (high TF-IDF, low frequency) — both should appear in union.
    jd = "build build build build pgvector"
    parsed = parse_jd(jd, limit=10)
    assert "build" in parsed.union_keywords
    assert "pgvector" in parsed.union_keywords
