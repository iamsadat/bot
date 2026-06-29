"""Onboarding helpers: resume text parsing and profile construction.

Used by the dashboard API to extract skills from a pasted resume and
build a UserProfile from the multi-step onboarding form.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from jobhunt.models import UserProfile

# ---------------------------------------------------------------------------
# Skill vocabulary — tokens that map to known engineering skills
# ---------------------------------------------------------------------------

_SKILLS_VOCAB: set[str] = {
    # Languages
    "python", "javascript", "typescript", "java", "go", "golang", "rust",
    "ruby", "scala", "kotlin", "swift", "cpp", "c++", "csharp", "c#",
    "php", "elixir", "haskell", "r",
    # Frontend
    "react", "vue", "angular", "nextjs", "svelte", "tailwind", "html", "css",
    "webpack", "vite",
    # Backend / frameworks
    "fastapi", "django", "flask", "rails", "spring", "express", "nestjs",
    "graphql", "grpc", "rest", "websocket",
    # Data stores
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis",
    "elasticsearch", "cassandra", "dynamodb", "bigquery", "snowflake",
    "pinecone", "pgvector",
    # Messaging / streams
    "kafka", "rabbitmq", "celery", "sqs", "pubsub", "kinesis",
    # Cloud / infra
    "aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform",
    "helm", "ansible", "pulumi", "cloudformation",
    # Observability / DevOps
    "opentelemetry", "prometheus", "grafana", "datadog", "jaeger",
    "jenkins", "github", "gitlab", "ci/cd", "cicd",
    # Data / ML / AI
    "spark", "hadoop", "airflow", "dbt", "pytorch", "tensorflow",
    "sklearn", "scikit-learn", "pandas", "numpy", "langchain", "langgraph",
    "llm", "openai", "anthropic", "rag",
    # Practices
    "microservices", "distributed", "observability", "tdd", "ddd",
    "agile", "scrum", "linux", "bash", "shell", "sql",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{1,}")
_YEAR_RE = re.compile(r"\b(20\d{2}|19[89]\d)\b")
_TITLE_RE = re.compile(
    r"(?:senior|staff|principal|lead|head\s+of|junior|mid(?:dle)?)?"
    r"\s*(?:software|backend|frontend|full[\s-]?stack|platform|infrastructure"
    r"|data|ml|ai|devops|sre|cloud|mobile|security)?"
    r"\s*(?:engineer|developer|architect|scientist|analyst|manager)\b",
    re.I,
)


def parse_resume_text(text: str) -> dict[str, Any]:
    """Extract structured metadata from pasted resume text.

    Returns a dict with:
      - skills: list[str] — matched engineering skills
      - inferred_titles: list[str] — job titles found in the text
      - experience_years: int | None — span of years mentioned
      - experiences/education/projects/links — best-effort structured sections
        for prefilling the builder UI (additive; never replaces the keys above).
    """
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))

    # Normalise variations (c++ → cpp, etc.) before matching
    normalised = {t.replace("+", "p").replace("#", "sharp") for t in tokens} | tokens
    skills = sorted(normalised & _SKILLS_VOCAB)

    titles = []
    seen: set[str] = set()
    for m in _TITLE_RE.finditer(text):
        t = " ".join(m.group().split())  # normalise whitespace
        if t and t.lower() not in seen:
            titles.append(t)
            seen.add(t.lower())

    years = sorted({int(y) for y in _YEAR_RE.findall(text)})
    experience_years: int | None = None
    if len(years) >= 2:
        experience_years = max(years) - min(years)

    result: dict[str, Any] = {
        "skills": skills,
        "inferred_titles": titles[:4],
        "experience_years": experience_years,
    }
    # Structured sections are best-effort; never let a parse failure drop the
    # primary keys above (the offline test suite depends on them).
    try:
        result.update(_parse_sections(text))
    except Exception:  # pragma: no cover - defensive
        result.setdefault("experiences", [])
        result.setdefault("education", [])
        result.setdefault("projects", [])
        result.setdefault("links", {})
    return result


# --------------------------------------------------------------------------- #
# Section-aware structured extraction
# --------------------------------------------------------------------------- #

_SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "experience": (
        "experience", "work experience", "employment", "professional experience",
        "work history",
    ),
    "education": ("education", "academic background"),
    "projects": ("projects", "personal projects", "selected projects", "side projects"),
    "skills": ("skills", "technical skills", "technologies"),
}
_DATE_RANGE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*)?"
    r"((?:19|20)\d{2})\s*(?:-|–|—|to)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*)?"
    r"((?:19|20)\d{2}|present|current|now)",
    re.I,
)
_BULLET_PREFIX_RE = re.compile(r"^\s*[-•*▪◦‣·]\s+")
_LINK_RE = re.compile(r"(https?://[^\s)]+|(?:www\.|github\.com/|linkedin\.com/)[^\s)]+)", re.I)


def _classify_heading(line: str) -> str | None:
    """Return the canonical section name if ``line`` looks like a heading."""
    stripped = line.strip().rstrip(":").lower()
    if not stripped or len(stripped) > 32:
        return None
    for canon, variants in _SECTION_HEADINGS.items():
        if stripped in variants:
            return canon
    return None


def _split_sections(text: str) -> dict[str, list[str]]:
    """Group lines under the most recent recognised heading."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        canon = _classify_heading(raw)
        if canon is not None:
            current = canon
            sections.setdefault(current, [])
            continue
        if current is not None and raw.strip():
            sections[current].append(raw.rstrip())
    return sections


def _extract_links(text: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for m in _LINK_RE.finditer(text):
        url = m.group(0).rstrip(".,")
        low = url.lower()
        if "github.com" in low and "github" not in links:
            links["github"] = url
        elif "linkedin.com" in low and "linkedin" not in links:
            links["linkedin"] = url
        elif low.startswith("http") and "website" not in links and "github" not in low and "linkedin" not in low:
            links["website"] = url
    return links


def _parse_experience_block(lines: list[str]) -> list[dict[str, Any]]:
    """Turn experience lines into entries split on date-range header lines."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        bullet_m = _BULLET_PREFIX_RE.match(line)
        if bullet_m and current is not None:
            current["bullets"].append(line[bullet_m.end():].strip())
            continue
        date_m = _DATE_RANGE_RE.search(line)
        header = _BULLET_PREFIX_RE.sub("", line).strip()
        if date_m or current is None:
            if current is not None:
                entries.append(current)
            start = (f"{date_m.group(1) or ''}{date_m.group(2)}".strip()
                     if date_m else "")
            end = (f"{date_m.group(3) or ''}{date_m.group(4)}".strip()
                   if date_m else "")
            # Strip the date span out of the header text to recover title/company.
            label = _DATE_RANGE_RE.sub("", header).strip(" |,–—-·\t")
            title, company, location = _split_role_label(label)
            current = {
                "title": title, "company": company, "location": location,
                "start": start, "end": end, "bullets": [], "skills": [],
            }
        elif current is not None and not current["bullets"]:
            # Continuation of the header (e.g. company on its own line).
            if not current["company"]:
                current["company"] = header
    if current is not None:
        entries.append(current)
    return [e for e in entries if e.get("title") or e.get("company") or e["bullets"]]


def _split_role_label(label: str) -> tuple[str, str, str]:
    """Split "Title, Company, Location" style headers into parts."""
    parts = [p.strip() for p in re.split(r"\s+[|@]\s+|,|—|–| - ", label) if p.strip()]
    title = parts[0] if parts else ""
    company = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""
    return title, company, location


def _parse_education_block(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in lines:
        clean = _BULLET_PREFIX_RE.sub("", line).strip()
        if not clean:
            continue
        date_m = _DATE_RANGE_RE.search(clean)
        years = _YEAR_RE.findall(clean)
        end = (date_m.group(4) if date_m else (years[-1] if years else ""))
        body = _DATE_RANGE_RE.sub("", clean).strip(" |,–—-·\t")
        parts = [p.strip() for p in re.split(r",|—|–| - | \| ", body) if p.strip()]
        school = parts[0] if parts else clean
        degree = parts[1] if len(parts) > 1 else ""
        entries.append({
            "school": school, "degree": degree, "field": "",
            "start": "", "end": str(end), "location": "",
        })
    return entries


def _parse_projects_block(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        bullet_m = _BULLET_PREFIX_RE.match(line)
        if bullet_m and current is not None:
            current["bullets"].append(line[bullet_m.end():].strip())
            continue
        header = line.strip()
        link_m = _LINK_RE.search(header)
        link = link_m.group(0).rstrip(".,") if link_m else ""
        name = (_LINK_RE.sub("", header).strip(" |,–—-·\t")
                if link else header)
        name = re.split(r",|—|–| - | \| ", name)[0].strip()
        if current is not None:
            entries.append(current)
        current = {"name": name, "description": "", "bullets": [],
                   "link": link, "skills": []}
    if current is not None:
        entries.append(current)
    return [e for e in entries if e.get("name")]


def _parse_sections(text: str) -> dict[str, Any]:
    sections = _split_sections(text)
    out: dict[str, Any] = {
        "experiences": [], "education": [], "projects": [],
        "links": _extract_links(text),
    }
    if sections.get("experience"):
        out["experiences"] = _parse_experience_block(sections["experience"])
    if sections.get("education"):
        out["education"] = _parse_education_block(sections["education"])
    if sections.get("projects"):
        out["projects"] = _parse_projects_block(sections["projects"])
    return out


class ResumeFileError(Exception):
    """Raised when an uploaded résumé file can't be read."""


def extract_resume_text(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded résumé (.txt/.docx/.pdf).

    DOCX uses python-docx; PDF uses pypdf when installed (optional). Plain text
    is decoded directly. Raises :class:`ResumeFileError` on unsupported types or
    a missing optional dependency, so the caller can return a clean 4xx.
    """
    name = (filename or "").lower()
    if name.endswith(".txt") or not name:
        try:
            return data.decode("utf-8", "ignore")
        except Exception as exc:  # pragma: no cover - defensive
            raise ResumeFileError(f"could not decode text: {exc}") from exc
    if name.endswith(".docx"):
        try:
            from io import BytesIO

            from docx import Document  # type: ignore
        except ImportError as exc:
            raise ResumeFileError("DOCX parsing needs python-docx") from exc
        doc = Document(BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    if name.endswith(".pdf"):
        try:
            from io import BytesIO

            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise ResumeFileError(
                "PDF parsing needs pypdf (pip install pypdf)"
            ) from exc
        reader = PdfReader(BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    raise ResumeFileError(f"unsupported file type: {filename!r} (use .txt/.docx/.pdf)")


def build_user_profile(form: dict[str, Any]) -> UserProfile:
    """Construct a UserProfile dataclass from validated onboarding form data."""
    return UserProfile(
        user_id=uuid.uuid4().hex,
        name=form["name"].strip(),
        email=form["email"].strip().lower(),
        phone=str(form.get("phone", "")).strip(),
        target_roles=[r.strip() for r in form.get("target_roles", []) if r.strip()],
        locations=[loc.strip() for loc in form.get("locations", []) if loc.strip()],
        min_salary=form.get("min_salary") or None,
        remote_ok=form.get("remote_ok", True),
        skills=[s.strip().lower() for s in form.get("skills", []) if s.strip()],
        culture_keywords=[c.strip() for c in form.get("culture_keywords", []) if c.strip()],
        experiences=form.get("experiences", []),
        education=form.get("education", []),
        projects=form.get("projects", []),
        links=dict(form.get("links", {})),
        veto_companies=[v.strip() for v in form.get("veto_companies", []) if v.strip()],
        weekly_target=int(form.get("weekly_target", 10)),
        auto_apply=bool(form.get("auto_apply", False)),
        daily_apply_cap=int(form.get("daily_apply_cap", 0)),
        relevance_floor=float(form.get("relevance_floor", 0.0)),
    )
