"""Parsing real file layouts, not just tidy inline strings.

Every other parser test feeds ``parse_resume_text`` a hand-written string with
clean headings and ``-`` bullets. Real uploads are not like that, and the repo
had **no PDF parsing test at all** — which is how a résumé that extracted to two
experience entries with none of its seven bullets went unnoticed.

Fixtures are generated in memory from dependencies already in the project
(``fpdf2`` for PDF, ``python-docx`` for DOCX) so nothing binary is committed.
"""

from __future__ import annotations

import base64
from io import BytesIO

import pytest

from jobhunt.onboarding import extract_resume_text, parse_resume_text

# Word's Symbol-font bullet. PDF extractors pass this private-use codepoint
# through verbatim, and it is what the résumé that prompted this work contained.
PUA_BULLET = ""

# Mirrors the layout of that résumé: a company on one line, the job title under
# it introduced by an em dash, then dates and place on a third line. Skills are
# given as labelled comma lists, which is how most résumés write them.
RESUME_LINES = [
    "Sadat Khan",
    "+91 99896 80670 | s@example.com | github.com/sadat",
    "Professional Summary",
    "Data Engineer with hands-on experience in SAS-to-PySpark migration.",
    "Experience",
    "SAS2PY",
    "— Junior Data Engineer (Former Data Engineering Intern)",
    "November 2025 – Present | Hyderabad, India",
    f"{PUA_BULLET} Worked on enterprise-scale SAS to PySpark migration projects.",
    f"{PUA_BULLET} Developed and optimized PySpark transformations and ETL workflows.",
    f"{PUA_BULLET} Assisted in converting legacy SAS DATA step logic into PySpark.",
    f"{PUA_BULLET} Worked with Databricks environments for notebook development.",
    f"{PUA_BULLET} Built reusable utilities for checkpointing and orchestration.",
    f"{PUA_BULLET} Collaborated with senior engineers on debugging data pipelines.",
    f"{PUA_BULLET} Improved understanding of partitioning and distributed execution.",
    "Education",
    "Lords Institute of Engineering and Technology",
    "— Bachelor of Engineering in Information Technology",
    "2021 – 2025 | Hyderabad, India",
    "Projects",
    "PEANut (Assessment Platform)",
    "— Built a quiz platform using Next.js, Prisma ORM, TypeScript and",
    "PlanetScale DB with secure authentication and teacher workflows.",
    "GoBank",
    "— Developed a backend banking system in Golang with PostgreSQL,",
    "account management, and transaction handling.",
    "Technical Skills",
    "Languages: Python, SQL, JavaScript, TypeScript, Java, Golang, C/C++",
    "Data Engineering: PySpark, Databricks, ETL Pipelines, SAS Migration",
    "Frameworks & Libraries: React, Next.js, Node.js, Express.js, Prisma ORM",
    "Databases: PostgreSQL, MongoDB, PlanetScale",
    "DevOps & Tools: Docker, Git, Kubernetes, CI/CD",
]
RESUME_TEXT = "\n".join(RESUME_LINES)


def _pdf_bytes() -> bytes:
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    # Real résumé PDFs encode core fonts as WinAnsi/cp1252 — that is where an
    # em dash becomes 0x97 and Word's bullet becomes an unmapped byte. latin-1,
    # fpdf's default, has no em dash at all.
    pdf.core_fonts_encoding = "cp1252"
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in RESUME_LINES:
        # Core PDF fonts are latin-1; the private-use bullet has no glyph there,
        # so write the byte a cp1252 round-trip produces. Extraction sees the
        # same unmapped character a real Word→PDF export leaves behind.
        pdf.cell(0, 5, line.replace(PUA_BULLET, "\x7f"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _docx_table_bytes() -> bytes:
    """A two-column résumé — i.e. a table, where the text is in cells."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    table = doc.add_table(rows=1, cols=2)
    left, right = table.rows[0].cells
    left.text = "\n".join(RESUME_LINES[:16])
    right.text = "\n".join(RESUME_LINES[16:])
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# The layout itself, independent of file format
# --------------------------------------------------------------------------- #

def test_private_use_bullets_are_recognised():
    """All seven bullets, not zero. This is the reported failure."""
    result = parse_resume_text(RESUME_TEXT)
    bullets = [b for e in result["experiences"] for b in e["bullets"]]
    assert len(bullets) == 7, bullets


def test_stacked_company_title_dates_layout_yields_one_entry():
    """Not two, the second of which was titled "Hyderabad" at "India"."""
    result = parse_resume_text(RESUME_TEXT)
    assert len(result["experiences"]) == 1, result["experiences"]
    entry = result["experiences"][0]
    assert entry["company"] == "SAS2PY"
    assert "Junior Data Engineer" in entry["title"]
    assert entry["start"] == "November 2025"
    assert entry["end"] == "Present"
    assert "Hyderabad" in entry["location"]


def test_one_degree_is_one_education_entry():
    result = parse_resume_text(RESUME_TEXT)
    assert len(result["education"]) == 1, result["education"]
    entry = result["education"][0]
    assert "Lords Institute" in entry["school"]
    assert "Bachelor of Engineering" in entry["degree"]
    assert entry["end"] == "2025"


def test_wrapped_descriptions_do_not_become_extra_projects():
    result = parse_resume_text(RESUME_TEXT)
    names = [p["name"] for p in result["projects"]]
    assert names == ["PEANut (Assessment Platform)", "GoBank"], names


def test_skills_section_is_read_as_an_open_vocabulary():
    """The closed 130-word list silently dropped most of this candidate's stack."""
    skills = set(parse_resume_text(RESUME_TEXT)["skills"])
    for expected in ("pyspark", "databricks", "next.js", "prisma orm", "git"):
        assert expected in skills, (expected, sorted(skills))


def test_experience_years_measures_the_job_history():
    """Not the span of every year in the document, which gave 5 for one year."""
    assert parse_resume_text(RESUME_TEXT)["experience_years"] <= 1


def test_target_roles_are_inferred_and_widened():
    titles = parse_resume_text(RESUME_TEXT)["inferred_titles"]
    assert any("Data Engineer" in t for t in titles)
    # The résumé names only data roles; its skills also support these.
    assert "Backend Engineer" in titles
    assert "Full Stack Engineer" in titles


def test_letter_spaced_and_uppercase_headings_are_recognised():
    text = RESUME_TEXT.replace("Experience", "E X P E R I E N C E")
    assert parse_resume_text(text)["experiences"]
    text = RESUME_TEXT.replace("Technical Skills", "SKILLS & TOOLS")
    assert "pyspark" in parse_resume_text(text)["skills"]


def test_prose_is_not_mistaken_for_a_heading():
    text = RESUME_TEXT.replace(
        f"{PUA_BULLET} Worked on enterprise-scale SAS to PySpark migration projects.",
        f"{PUA_BULLET} Selected the right tools for the job, including projects tooling.",
    )
    # Still one experience entry: the bullet did not open a "Projects" section.
    assert len(parse_resume_text(text)["experiences"]) == 1


# --------------------------------------------------------------------------- #
# File formats
# --------------------------------------------------------------------------- #

def test_pdf_upload_round_trips_through_the_parser():
    pytest.importorskip("pypdf")
    text = extract_resume_text("resume.pdf", _pdf_bytes())
    result = parse_resume_text(text)
    assert result["experiences"], text[:400]
    bullets = [b for e in result["experiences"] for b in e["bullets"]]
    assert len(bullets) >= 6, bullets
    assert "pyspark" in set(result["skills"])


def test_docx_table_cells_are_extracted():
    """Paragraph-only extraction returned nothing for a two-column résumé."""
    text = extract_resume_text("resume.docx", _docx_table_bytes())
    assert "PySpark" in text
    assert "Lords Institute" in text
    assert parse_resume_text(text)["experiences"]


def test_upload_endpoint_accepts_a_pdf():
    pytest.importorskip("pypdf")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jobhunt.dashboard.server import DashboardState, create_app
    from jobhunt.trace import ThoughtBus, TraceStore

    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    client = TestClient(create_app(state))
    client.post("/api/onboarding/profile", json={
        "name": "Sadat", "email": "s@example.com",
        "target_roles": ["data engineer"], "locations": ["Hyderabad"]})
    payload = {
        "filename": "resume.pdf",
        "content_base64": base64.b64encode(_pdf_bytes()).decode(),
    }
    res = client.post("/api/profile/parse-resume-file", json=payload)
    assert res.status_code == 200, res.text
    assert "pyspark" in set(res.json()["skills"])
