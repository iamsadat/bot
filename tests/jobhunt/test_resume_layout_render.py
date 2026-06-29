"""Tests for the draft-aware layout renderers (PDF/DOCX/HTML)."""

from __future__ import annotations

import pytest

from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_renderer import (
    RendererUnavailable, draft_to_docx, draft_to_pdf, draft_to_styled_html,
)
from jobhunt.resume_template import build_tailored_resume


def _draft():
    profile = UserProfile(
        user_id="u1", name="Ada Lovelace", email="ada@x.com", phone="555",
        target_roles=["backend"], locations=["Remote"],
        skills=["python", "kubernetes"],
        experiences=[{
            "title": "Senior Backend Engineer", "company": "Globex",
            "location": "Remote", "start": "Jan 2019", "end": "Present",
            "bullets": ["Built distributed Python services on Kubernetes."],
        }],
        education=[{"school": "MIT", "degree": "BSc", "end": "2019"}],
        links={"github": "github.com/ada"},
    )
    posting = JobPosting(
        job_id="j1", source="gh", source_id="1", url="https://x/y",
        title="Backend Engineer", company="Acme", location="Remote",
        jd_text="Python Kubernetes backend services.",
    )
    return build_tailored_resume(profile, posting)


def test_pdf_is_nonempty_and_valid():
    try:
        out = draft_to_pdf(_draft())
    except RendererUnavailable:
        pytest.skip("fpdf2 not installed")
    assert out[:4] == b"%PDF"
    assert len(out) > 800


def test_html_has_right_aligned_rows_and_sections():
    html = draft_to_styled_html(_draft())
    assert "Ada Lovelace" in html
    assert 'class="right"' in html
    assert "Experience" in html
    assert "Education" in html
    assert "<strong>" in html  # bold title/company


def test_docx_opens_and_contains_headings():
    try:
        out = draft_to_docx(_draft())
    except RendererUnavailable:
        pytest.skip("python-docx not installed")
    from io import BytesIO

    from docx import Document  # type: ignore
    doc = Document(BytesIO(out))
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "Ada Lovelace" in full
    assert "Experience" in full


def test_long_title_does_not_crash_pdf():
    d = _draft()
    long_title = "Extremely Senior Principal Distinguished Staff Backend Platform Engineer"
    d.sections[0].rows[0]["left"] = f"**{long_title}, A Very Long Company Name LLC**"
    try:
        out = draft_to_pdf(d)
    except RendererUnavailable:
        pytest.skip("fpdf2 not installed")
    assert out[:4] == b"%PDF"
