"""Resume rendering: plain text (stdlib), PDF (WeasyPrint), DOCX (python-docx).

Each renderer returns ``bytes`` so the result can be uploaded straight to S3.
Optional dependencies degrade gracefully — if WeasyPrint or python-docx are
missing, the renderer raises ``RendererUnavailable`` and the caller can fall
back to text. This means tests stay dependency-free.
"""

from __future__ import annotations

from io import BytesIO
from typing import Protocol

from jobhunt.resume_template import ResumeDraft


class RendererUnavailable(RuntimeError):
    """Raised when an optional renderer dependency is missing."""


class Renderer(Protocol):
    extension: str
    content_type: str

    def render(self, draft: ResumeDraft) -> bytes: ...


# ---------------------------------------------------------- plain text


class TextRenderer:
    extension = "txt"
    content_type = "text/plain"

    def render(self, draft: ResumeDraft) -> bytes:
        return draft.to_text().encode("utf-8")


# ----------------------------------------------------------------- HTML


def _draft_to_html(draft: ResumeDraft) -> str:
    """Inline-styled HTML — works for both browser preview and WeasyPrint PDF."""
    sections_html: list[str] = []
    for sec in draft.sections:
        sec_html = [f"<h2>{sec.title}</h2>"]
        if sec.body:
            sec_html.append(f"<p>{sec.body}</p>")
        if sec.bullets:
            sec_html.append("<ul>")
            for b in sec.bullets:
                sec_html.append(
                    f'<li data-evidence-id="{b.evidence_id}">{b.text}</li>'
                )
            sec_html.append("</ul>")
        sections_html.append("\n".join(sec_html))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{draft.candidate_name} — {draft.target_role}</title>
<style>
  body {{ font-family: -apple-system, Helvetica, sans-serif; max-width: 720px;
          margin: 32px auto; color: #1a1a1a; line-height: 1.45; }}
  h1 {{ font-size: 1.6em; margin-bottom: 0.2em; }}
  h2 {{ font-size: 1.1em; border-bottom: 1px solid #ccc; margin-top: 1.4em; }}
  .meta {{ color: #666; }}
  ul {{ padding-left: 1.2em; }}
  li {{ margin-bottom: 0.3em; }}
</style></head><body>
  <h1>{draft.candidate_name}</h1>
  <div class="meta">{draft.candidate_email}</div>
  <div class="meta">Target: {draft.target_role} @ {draft.target_company}</div>
  <h2>Summary</h2>
  <p>{draft.summary}</p>
  {chr(10).join(sections_html)}
</body></html>"""


class HTMLRenderer:
    extension = "html"
    content_type = "text/html"

    def render(self, draft: ResumeDraft) -> bytes:
        return _draft_to_html(draft).encode("utf-8")


# -------------------------------------------------------------- PDF (WeasyPrint)


class PDFRenderer:
    extension = "pdf"
    content_type = "application/pdf"

    def render(self, draft: ResumeDraft) -> bytes:
        # Pure-Python fpdf2 — no system libraries, installs cleanly on
        # Windows and in Docker (unlike WeasyPrint's GTK/Pango stack).
        body = draft.to_text()
        lines = body.split("\n")
        heading = lines[0].strip() if lines and lines[0].strip() else draft.candidate_name
        return text_to_pdf(heading, "\n".join(lines[1:]))


# --------------------------------------------------------------- DOCX


class DOCXRenderer:
    extension = "docx"
    content_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    def render(self, draft: ResumeDraft) -> bytes:
        try:
            from docx import Document  # type: ignore
        except ImportError as e:
            raise RendererUnavailable(
                "python-docx not installed; pip install python-docx"
            ) from e

        doc = Document()
        doc.add_heading(draft.candidate_name, level=0)
        doc.add_paragraph(draft.candidate_email)
        doc.add_paragraph(
            f"Target: {draft.target_role} @ {draft.target_company}"
        )

        doc.add_heading("Summary", level=2)
        doc.add_paragraph(draft.summary)

        for sec in draft.sections:
            doc.add_heading(sec.title, level=2)
            if sec.body:
                doc.add_paragraph(sec.body)
            for b in sec.bullets:
                doc.add_paragraph(b.text, style="List Bullet")

        buf = BytesIO()
        doc.save(buf)
        return buf.getvalue()


# ----------------------------------------------------------------- helpers
#
# These take a heading + a structured plain-text body (the format produced by
# ResumeArchitectAgent._render_resume: ALL-CAPS section headers, "- " bullets,
# blank-line separated paragraphs) and render clean PDF / HTML / DOCX. They
# back the dashboard download endpoint so every format shares one layout.

# Core PDF fonts are latin-1 only; transliterate the handful of unicode
# punctuation our text can contain so fpdf2 never raises on an exotic glyph.
_TRANSLIT = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "•": "-", "…": "...",
    "→": "->", "←": "<-", "≥": ">=", "≤": "<=",
    " ": " ", " ": " ", "​": "",
}


def _latin1(s: str) -> str:
    for k, v in _TRANSLIT.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _is_heading(line: str) -> bool:
    """Section headers are short, all-caps, alphabetic lines."""
    s = line.strip()
    return bool(s) and len(s) <= 40 and s == s.upper() and any(c.isalpha() for c in s)


def _is_bullet(line: str) -> bool:
    return line.lstrip().startswith(("- ", "* ", "• "))


def _bullet_text(line: str) -> str:
    return line.lstrip()[2:].strip()


def text_to_pdf(heading: str, body: str) -> bytes:
    """Render a heading + structured body to a clean A4 PDF via fpdf2."""
    try:
        from fpdf import FPDF  # type: ignore
    except ImportError as e:
        raise RendererUnavailable("fpdf2 not installed; pip install fpdf2") from e

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()

    # multi_cell leaves the cursor at the right margin by default, which makes
    # the *next* full-width cell see zero available width; LMARGIN/NEXT returns
    # the cursor to the left margin on the following line.
    def cell(text: str, h: float) -> None:
        pdf.multi_cell(0, h, _latin1(text), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(17, 17, 24)
    pdf.set_font("Helvetica", "B", 19)
    cell(heading, 9)
    pdf.ln(1)

    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(2)
            continue
        if _is_heading(line):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(90, 92, 120)
            cell(line.strip(), 6)
            pdf.set_text_color(30, 30, 38)
            continue
        if _is_bullet(line):
            pdf.set_font("Helvetica", size=10)
            cell("-  " + _bullet_text(line), 5)
            continue
        pdf.set_font("Helvetica", size=10)
        cell(line, 5)

    return bytes(pdf.output())


def text_to_styled_html(heading: str, body: str, *, tab_title: str = "") -> str:
    """Render a heading + structured body to a self-contained styled HTML page."""
    blocks: list[str] = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            blocks.append("<ul>" + "".join(f"<li>{_esc(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if _is_heading(line):
            flush()
            blocks.append(f"<h2>{_esc(line.strip().title())}</h2>")
            continue
        if _is_bullet(line):
            bullets.append(_bullet_text(line))
            continue
        flush()
        blocks.append(f"<p>{_esc(line)}</p>")
    flush()

    inner = "\n  ".join(blocks)
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{_esc(tab_title or heading)}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
          max-width: 720px; margin: 40px auto; padding: 0 24px;
          color: #14161f; line-height: 1.5; }}
  h1 {{ font-size: 1.7em; margin: 0 0 4px; letter-spacing: -0.02em; }}
  h2 {{ font-size: 0.82em; text-transform: uppercase; letter-spacing: 0.09em;
        color: #5b6478; border-bottom: 1px solid #e3e6ef;
        padding-bottom: 4px; margin: 26px 0 10px; }}
  p {{ margin: 0 0 8px; }}
  ul {{ margin: 0 0 8px; padding-left: 20px; }}
  li {{ margin-bottom: 5px; }}
</style></head><body>
  <h1>{_esc(heading)}</h1>
  {inner}
</body></html>"""


def text_to_docx(heading: str, body: str) -> bytes:
    """Render a heading + structured body to a .docx via python-docx."""
    try:
        from docx import Document  # type: ignore
    except ImportError as e:
        raise RendererUnavailable(
            "python-docx not installed; pip install python-docx"
        ) from e

    doc = Document()
    doc.add_heading(heading, level=0)
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if _is_heading(line):
            doc.add_heading(line.strip().title(), level=2)
        elif _is_bullet(line):
            doc.add_paragraph(_bullet_text(line), style="List Bullet")
        else:
            doc.add_paragraph(line)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------- factory


def get_renderer(fmt: str) -> Renderer:
    """Return a renderer by extension. Falls back to text on unknown fmt."""
    table: dict[str, type] = {
        "txt": TextRenderer,
        "html": HTMLRenderer,
        "pdf": PDFRenderer,
        "docx": DOCXRenderer,
    }
    cls = table.get(fmt.lower(), TextRenderer)
    return cls()
