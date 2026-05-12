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
        try:
            from weasyprint import HTML  # type: ignore
        except ImportError as e:
            raise RendererUnavailable(
                "WeasyPrint not installed; pip install weasyprint"
            ) from e
        html = _draft_to_html(draft)
        buf = BytesIO()
        HTML(string=html).write_pdf(target=buf)
        return buf.getvalue()


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
