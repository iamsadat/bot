"""Tests for the Phase-2 templated resume engine and renderers.

Validates:
* Evidence-id invariant — every produced bullet is backed by a profile node.
* LLM tone-rewrite is a best-effort layer (no hallucination on top).
* Renderers produce valid bytes; optional PDF/DOCX degrade if deps missing.
"""

from __future__ import annotations

import pytest

from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_template import (
    build_evidence_index,
    build_resume_draft,
)
from jobhunt.resume_renderer import (
    TextRenderer,
    HTMLRenderer,
    PDFRenderer,
    DOCXRenderer,
    RendererUnavailable,
    get_renderer,
)


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u-1",
        name="Ada Lovelace",
        email="ada@example.com",
        target_roles=["backend engineer"],
        locations=["Remote"],
        skills=["python", "kubernetes", "postgres"],
        experiences=[
            {"title": "Senior Backend Engineer", "company": "Globex",
             "highlight": "Built distributed Python services on Kubernetes."},
        ],
    )


def _posting() -> JobPosting:
    return JobPosting(
        job_id="j-1",
        source="greenhouse",
        source_id="g-1",
        url="https://example.com/jobs/1",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        jd_text="Python on Kubernetes. Postgres a plus.",
    )


def test_build_evidence_index_includes_skills_and_experience_terms():
    ev = build_evidence_index(_profile())
    assert "python" in ev
    assert "kubernetes" in ev
    # Experience tokens get indexed too.
    assert "distributed" in ev
    assert ev["python"]["id"].startswith("skill:")


def test_build_resume_draft_every_bullet_has_evidence_id():
    draft = build_resume_draft(
        _profile(), _posting(),
        keywords=["python", "kubernetes", "rust"],
    )
    assert all(b.evidence_id for b in draft.all_bullets())
    # "rust" not in profile — should land in missing, not in bullets.
    assert "rust" in draft.missing_keywords
    assert all(b.keyword != "rust" for b in draft.all_bullets())
    assert "python" in draft.matched_keywords
    assert "kubernetes" in draft.matched_keywords


def test_llm_rewrite_is_best_effort_and_keeps_evidence_id():
    def fake_llm(action, payload):
        if action == "rewrite_bullet":
            return f"Polished bullet about {payload['keyword']}"
        if action == "summary":
            return "Polished LLM summary."
        return ""

    draft = build_resume_draft(
        _profile(), _posting(),
        keywords=["python"],
        llm=fake_llm,
    )
    assert draft.summary == "Polished LLM summary."
    bullets = draft.all_bullets()
    assert bullets[0].text == "Polished bullet about python"
    assert bullets[0].rewritten_by_llm is True
    # Evidence id is still set — LLM cannot detach a bullet from evidence.
    assert bullets[0].evidence_id.startswith("skill:")


def test_llm_exception_falls_back_to_deterministic_phrasing():
    def broken_llm(action, payload):
        raise RuntimeError("LLM rate limited")

    draft = build_resume_draft(
        _profile(), _posting(),
        keywords=["python"],
        llm=broken_llm,
    )
    bullets = draft.all_bullets()
    assert bullets[0].text.startswith("Delivered work involving python")
    assert bullets[0].rewritten_by_llm is False


def test_text_renderer_returns_utf8_bytes():
    draft = build_resume_draft(
        _profile(), _posting(),
        keywords=["python", "kubernetes"],
    )
    out = TextRenderer().render(draft)
    assert isinstance(out, bytes)
    decoded = out.decode("utf-8")
    assert "Ada Lovelace" in decoded
    assert "Senior Backend Engineer" in decoded


def test_html_renderer_includes_evidence_id_attribute():
    draft = build_resume_draft(
        _profile(), _posting(),
        keywords=["python"],
    )
    html = HTMLRenderer().render(draft).decode("utf-8")
    assert "<!DOCTYPE html>" in html
    assert 'data-evidence-id="skill:0"' in html


def test_get_renderer_falls_back_to_text_for_unknown_format():
    r = get_renderer("unknown-fmt")
    assert isinstance(r, TextRenderer)


def test_get_renderer_returns_correct_class_per_extension():
    assert isinstance(get_renderer("txt"), TextRenderer)
    assert isinstance(get_renderer("html"), HTMLRenderer)
    assert isinstance(get_renderer("pdf"), PDFRenderer)
    assert isinstance(get_renderer("docx"), DOCXRenderer)


def test_pdf_renderer_raises_when_dep_missing():
    """If WeasyPrint isn't installed the renderer raises a clean exception."""
    draft = build_resume_draft(_profile(), _posting(), keywords=["python"])
    try:
        import weasyprint  # noqa: F401
        # Installed — render should succeed.
        out = PDFRenderer().render(draft)
        assert isinstance(out, bytes) and out[:4] == b"%PDF"
    except ImportError:
        with pytest.raises(RendererUnavailable):
            PDFRenderer().render(draft)


def test_docx_renderer_raises_when_dep_missing():
    draft = build_resume_draft(_profile(), _posting(), keywords=["python"])
    try:
        import docx  # noqa: F401
        out = DOCXRenderer().render(draft)
        # DOCX files start with PK (zip).
        assert out[:2] == b"PK"
    except ImportError:
        with pytest.raises(RendererUnavailable):
            DOCXRenderer().render(draft)
