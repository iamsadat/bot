"""Onboarding helpers: resume text parsing and profile construction.

Used by the dashboard API to extract skills from a pasted resume and
build a UserProfile from the multi-step onboarding form.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from typing import Any

from jobhunt.models import UserProfile

logger = logging.getLogger(__name__)

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
        if t.isupper():
            t = t.title()  # a "DATA ENGINEER" header line
        if t and t.lower() not in seen:
            titles.append(t)
            seen.add(t.lower())

    result: dict[str, Any] = {
        "skills": skills,
        "inferred_titles": _widen_titles(titles, skills),
    }
    # Structured sections are best-effort; never let a parse failure drop the
    # primary keys above (the offline test suite depends on them).
    result["contact"] = contact_from_text(text)
    try:
        result.update(_parse_sections(text))
    except Exception:  # pragma: no cover - defensive
        logger.exception("structured résumé parse failed; returning skills only")
        result.setdefault("experiences", [])
        result.setdefault("education", [])
        result.setdefault("projects", [])
        result.setdefault("links", {})

    # Skills the résumé lists explicitly, on top of the closed-vocabulary scan.
    # Stored verbatim, not canonicalised: the taxonomy groups a skill with its
    # ecosystem ("fastapi" sits with "python"), which is what makes *matching*
    # forgiving but would erase real detail from the candidate's own list.
    # Canonicalisation happens at match time in agents.discovery.skill_fit.
    listed = result.pop("section_skills", [])
    seen_skill: set[str] = set()
    merged_skills: list[str] = []
    from jobhunt.skill_names import key as skill_key

    for s in (*skills, *listed):
        key = s.strip().lower()
        # Fragments of a listed group ("Bronze/Silver/Gold", "MERGE, OPTIMIZE")
        # are not skills on their own.
        if key and skill_key(key) and key not in seen_skill:
            seen_skill.add(key)
            merged_skills.append(key)
    result["skills"] = sorted(merged_skills)

    result["experience_years"] = _experience_years(text, result["experiences"])
    scrub_placeholders(result)
    # Named from the whole text: the header's "[Phone] | [Email]" is in no field.
    result["placeholders"] = list(dict.fromkeys(
        m.group(1).strip() for m in _PLACEHOLDER_RE.finditer(text)))
    return result


# Adjacent roles a candidate is qualified for, keyed by the skills that show it.
# Titles found verbatim in a résumé are a narrow slice of what somebody can
# apply to: this candidate's résumé names only "Data Engineer" while its own
# summary claims a "strong backend and full-stack engineering foundation". Since
# these titles now prefill the search, a two-role prefill returns a nearly empty
# board — so the obvious neighbours are offered too, for the user to trim.
_ROLE_EVIDENCE: tuple[tuple[str, frozenset[str]], ...] = (
    ("Data Engineer", frozenset({
        "pyspark", "spark", "etl", "databricks", "airflow", "hadoop", "sql",
        "snowflake", "dbt", "sas",
    })),
    ("Backend Engineer", frozenset({
        "golang", "go", "java", "express", "node", "nodejs", "spring", "django",
        "flask", "fastapi", "postgresql", "postgres", "mongodb", "microservices",
    })),
    ("Full Stack Engineer", frozenset({
        "react", "nextjs", "typescript", "javascript", "node", "nodejs",
        "prisma", "tailwind",
    })),
    ("Software Engineer", frozenset()),
)

# How much evidence a suggested role needs, and how many roles to offer. The cap
# matters: each role multiplies the number of source requests per sweep.
_ROLE_MIN_EVIDENCE = 2
_MAX_TITLES = 5


def _widen_titles(found: list[str], skills: list[str]) -> list[str]:
    """Titles from the résumé, plus adjacent roles its skills support."""
    have = {s.lower() for s in skills}
    out = list(found[:3])
    seen = {t.lower() for t in out}
    for role, evidence in _ROLE_EVIDENCE:
        if role.lower() in seen:
            continue
        # The catch-all generalist title only earns a place on a résumé with
        # real engineering breadth; otherwise it drags in unrelated roles.
        enough = (
            len(have) >= 4 if not evidence
            else len(have & evidence) >= _ROLE_MIN_EVIDENCE
        )
        if enough:
            out.append(role)
            seen.add(role.lower())
    return out[:_MAX_TITLES]


def _experience_years(text: str, experiences: list[dict[str, Any]]) -> int | None:
    """Years of professional experience, measured from the job history.

    Reads the dated *experience* entries rather than every 4-digit number in the
    document. Spanning the whole résumé counted degree start years, project
    years and copyright footers, so a candidate who began their first job in
    2025 was scored at 5 years' experience — which then made every Staff role
    look plausible.

    Falls back to the document-wide span only when no experience entry carries a
    date, since a résumé with no recognised experience section is the one case
    where the crude estimate beats no estimate at all.
    """
    spans = [
        (start, end)
        for e in experiences
        if (start := _first_year(e.get("start"))) is not None
        and (end := _end_year(e.get("end"))) is not None
    ]
    if spans:
        # Union of the spans, not their sum: overlapping roles are one career,
        # and a promotion listed as two entries is not two careers.
        return max(max(e for _, e in spans) - min(s for s, _ in spans), 0)

    years = sorted({int(y) for y in _YEAR_RE.findall(text)})
    return max(years) - min(years) if len(years) >= 2 else None


def _first_year(value: Any) -> int | None:
    m = _YEAR_RE.search(str(value or ""))
    return int(m.group(1)) if m else None


def _end_year(value: Any) -> int | None:
    """Year an entry ended; open-ended entries end today."""
    raw = str(value or "").strip().lower()
    if raw in {"present", "current", "now", "date", "ongoing", "till date"}:
        return date.today().year
    return _first_year(raw)


# --------------------------------------------------------------------------- #
# Section-aware structured extraction
# --------------------------------------------------------------------------- #

# Words that identify a section. A heading is recognised when its words are a
# subset of one of these sets, which covers the variants real résumés use
# ("TECHNICAL SKILLS", "Skills & Tools", "Employment History", "Relevant
# Experience") without needing every phrasing enumerated. Exact whole-line
# matching missed all of those and returned an empty structure behind an HTTP
# 200, so a résumé looked parsed when nothing had been read.
_SECTION_WORDS: dict[str, frozenset[str]] = {
    "experience": frozenset({
        "experience", "work", "employment", "professional", "history",
        "relevant", "career", "positions",
    }),
    "education": frozenset({
        "education", "academic", "background", "qualifications", "degrees",
    }),
    "projects": frozenset({
        "projects", "project", "personal", "selected", "side", "portfolio",
        "notable",
    }),
    "skills": frozenset({
        "skills", "skill", "technical", "technologies", "technology", "tools",
        "stack", "competencies", "core", "expertise", "proficiencies",
    }),
    "summary": frozenset({
        "summary", "professional", "profile", "objective", "about", "overview",
    }),
    # Not projects: "Open Source & Problem Solving" was parsed as one.
    "achievements": frozenset({
        "achievements", "awards", "honors", "honours", "certifications",
        "certificates", "publications", "activities", "extracurricular",
        "open", "source", "problem", "solving", "and", "leadership", "key",
    }),
}

# A heading must contain at least one of these, so a bullet like "Selected the
# best tools for the job" cannot be mistaken for a "Selected Projects" heading.
_SECTION_ANCHORS: dict[str, frozenset[str]] = {
    "experience": frozenset({"experience", "employment", "history", "career", "positions"}),
    "education": frozenset({"education", "academic", "degrees", "qualifications"}),
    "projects": frozenset({"projects", "project", "portfolio"}),
    "skills": frozenset({"skills", "skill", "technologies", "technology", "competencies", "expertise", "stack"}),
    "summary": frozenset({"summary", "profile", "objective", "overview", "about"}),
    "achievements": frozenset({
        "achievements", "awards", "honors", "honours", "certifications",
        "certificates", "publications", "activities", "extracurricular", "open",
    }),
}
_DATE_RANGE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*)?"
    r"((?:19|20)\d{2})\s*(?:-|–|—|to)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*)?"
    r"((?:19|20)\d{2}|present|current|now)",
    re.I,
)
# Bullet glyphs, and the space after one is optional. PDF text extraction often
# loses that space, and Word→PDF emits U+F0B7 — a private-use codepoint — for a
# plain bullet, which pypdf passes through verbatim. Missing those meant every
# bullet line was read as the header of a brand-new job: one résumé produced two
# experience entries with none of its seven bullets.
_BULLET_PREFIX_RE = re.compile(
    # U+F0B7 is Word's Symbol-font bullet, which PDF extractors pass through
    # verbatim; U+007F is what a cp1252 decode of the same glyph produces.
    "^\\s*[-\u2022*\u25aa\u25e6\u2023\xb7\u25cb\u25cf\u25b6\u25ba\xbb\u2192\u2043\u25fe\u2714\u2713\uf0b7\x7f]\\s*"
    "|^\\s*\\d{1,2}[.)]\\s+"
)
_LINK_RE = re.compile(r"(https?://[^\s)]+|(?:www\.|github\.com/|linkedin\.com/)[^\s)]+)", re.I)


def _classify_heading(line: str) -> str | None:
    """Return the canonical section name if ``line`` looks like a heading.

    Matches on words rather than the whole line, so "TECHNICAL SKILLS",
    "Skills & Tools" and "Employment History" are all recognised. A heading has
    to be short, has to contain an anchor word for its section, and cannot
    contain anything outside that section's vocabulary — which is what keeps a
    sentence like "Selected the right tools for the job" from being read as a
    "Selected Projects" heading.
    """
    stripped = line.strip().strip(":-–—•").strip()
    if not stripped or len(stripped) > 40:
        return None
    # Letter-spaced headings ("E X P E R I E N C E") collapse to one word.
    letters = stripped.replace(" ", "")
    if len(stripped) > 2 and all(
        c.isupper() or not c.isalpha() for c in stripped
    ) and stripped.count(" ") >= len(letters) - 1:
        stripped = letters

    tokens = {t for t in re.split(r"[^a-z]+", stripped.lower()) if t}
    if not tokens:
        return None
    for canon, vocab in _SECTION_WORDS.items():
        if tokens <= vocab and tokens & _SECTION_ANCHORS[canon]:
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


# Words that make a line read as a job title rather than a place or a date.
_ROLE_NOUNS = frozenset({
    "engineer", "engineering", "developer", "development", "analyst", "manager",
    "architect", "scientist", "consultant", "specialist", "designer", "intern",
    "internship", "lead", "head", "director", "administrator", "associate",
    "president", "founder", "officer", "programmer", "technician", "researcher",
    "trainee", "apprentice",
})


def _names_a_role(text: str) -> bool:
    """Whether a line plausibly names a job rather than a place or a date."""
    tokens = {t for t in re.split(r"[^a-z]+", (text or "").lower()) if t}
    return bool(tokens & _ROLE_NOUNS)


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
        # Strip the date span out of the header text to recover title/company.
        label = (_DATE_RANGE_RE.sub("", header).strip(" |,–—-·\t")
                 if date_m else header)

        # A date line that carries no job title belongs to the entry above it.
        # Résumés commonly stack "Company / — Title / Dates | Place", and
        # treating that third line as a new job invented an entry titled
        # "Hyderabad" at a company called "India" — and left the real entry
        # without its dates.
        if date_m and current is not None and not current["start"] and not _names_a_role(label):
            current["start"] = f"{date_m.group(1) or ''}{date_m.group(2)}".strip()
            current["end"] = f"{date_m.group(3) or ''}{date_m.group(4)}".strip()
            if label and not current["location"]:
                current["location"] = label
            continue

        if date_m or current is None:
            if current is not None:
                entries.append(current)
            start = (f"{date_m.group(1) or ''}{date_m.group(2)}".strip()
                     if date_m else "")
            end = (f"{date_m.group(3) or ''}{date_m.group(4)}".strip()
                   if date_m else "")
            title, company, location = _split_role_label(label)
            current = {
                "title": title, "company": company, "location": location,
                "start": start, "end": end, "bullets": [], "skills": [],
            }
        elif current is not None and not current["bullets"]:
            # Continuation of the header. A line introduced by a dash is the job
            # title under a company name ("SAS2PY" / "— Junior Data Engineer"),
            # so the line above it was the employer.
            dashed = re.match(r"^\s*[—–-]\s*(?P<rest>.+)$", header)
            if dashed and _names_a_role(dashed.group("rest")) and not current["company"]:
                current["company"] = current["title"]
                current["title"] = dashed.group("rest").strip()
            elif not current["company"]:
                current["company"] = header
    if current is not None:
        entries.append(current)
    return [e for e in entries if e.get("title") or e.get("company") or e["bullets"]]


def _split_role_label(label: str) -> tuple[str, str, str]:
    """Split "Title, Company, Location" style headers into parts.

    Splits on the separators résumés actually use, including a bare em/en dash
    and the word "at" ("Senior Engineer at Globex"). Commas only separate when
    the piece that follows is not a company suffix — splitting on every comma
    turned "Globex, Inc., Remote" into title "Globex", company "Inc.".
    """
    parts = [
        p.strip(" .")
        for p in re.split(r"\s+[|@]\s+|\s+at\s+|\s*[—–]\s*|\s+-\s+|,", label)
        if p.strip(" .")
    ]
    merged: list[str] = []
    for part in parts:
        # "Inc", "Ltd", "GmbH" belong to the name before them, not a new field.
        if merged and part.lower().rstrip(".") in _COMPANY_SUFFIXES:
            merged[-1] = f"{merged[-1]}, {part}"
        else:
            merged.append(part)
    title = merged[0] if merged else ""
    company = merged[1] if len(merged) > 1 else ""
    location = merged[2] if len(merged) > 2 else ""
    return title, company, location


_COMPANY_SUFFIXES = frozenset({
    "inc", "llc", "ltd", "limited", "gmbh", "plc", "corp", "corporation",
    "co", "company", "pvt", "private", "ag", "bv", "sa", "srl", "oy", "ab",
})


# Words that mark a line as naming a qualification rather than an institution.
_DEGREE_WORDS = frozenset({
    "bachelor", "bachelors", "master", "masters", "b", "bs", "ba", "bsc",
    "btech", "be", "ms", "msc", "mtech", "mba", "phd", "doctorate", "diploma",
    "associate", "engineering", "technology", "science", "arts", "commerce",
    "in", "of", "the", "and",
})

# Institution words win over degree words. "Lords Institute of Engineering and
# Technology" is a school, but it contains "Engineering" and "Technology", so a
# degree-word test alone filed the college as the qualification.
_INSTITUTION_WORDS = frozenset({
    "university", "institute", "institution", "college", "school", "academy",
    "polytechnic", "iit", "nit", "bits", "iiit",
})


def _parse_education_block(lines: list[str]) -> list[dict[str, Any]]:
    """Group education lines into one entry per qualification.

    A degree is usually spread over two or three lines — institution, then
    qualification, then dates and place. Emitting one entry per *line* turned a
    single degree into three, of which two were nonsense ("Hyderabad" as a school
    with "India" as the degree).
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and (current["school"] or current["degree"]):
            entries.append(current)
        current = None

    for line in lines:
        clean = _BULLET_PREFIX_RE.sub("", line).strip()
        if not clean:
            continue
        date_m = _DATE_RANGE_RE.search(clean)
        years = _YEAR_RE.findall(clean)
        body = _DATE_RANGE_RE.sub("", clean).strip(" |,–—-·\t")
        # A lone graduation year is not a date *range*, so it survives the strip
        # above and would end up glued to the degree ("BSc Computer Science 2019").
        body = _YEAR_RE.sub("", body).strip(" |,–—-·\t")
        parts = [p.strip(" |,–—-·\t") for p in re.split(r"\s*[—–|]\s*|,", body) if p.strip(" |,–—-·\t")]

        # Classify each part rather than the whole line: "MIT — BSc Computer
        # Science" carries the institution and the qualification together.
        degree = next((p for p in parts if _is_degree(p)), "")
        school = next((p for p in parts if _is_institution(p)), "")
        if not school:
            school = next((p for p in parts if p != degree and not _is_place(p)), "")
        place = [p for p in parts if p not in {degree, school} and _is_place(p)]

        if degree or school:
            # A part we already hold means this is the next qualification.
            if current is not None and (
                (school and current["school"]) or (degree and current["degree"])
            ):
                flush()
            if current is None:
                current = _blank_education()
            current["school"] = current["school"] or school
            current["degree"] = current["degree"] or degree

        if current is not None:
            if place and not current["location"]:
                current["location"] = ", ".join(place)
            if date_m:
                current["start"] = current["start"] or str(date_m.group(2) or "")
                current["end"] = str(date_m.group(4) or current["end"])
            elif years:
                current["end"] = current["end"] or str(years[-1])
    flush()
    return entries


def _tokens_of(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z]+", (text or "").lower()) if t}


def _is_degree(part: str) -> bool:
    tokens = _tokens_of(part)
    return bool(tokens & _DEGREE_WORDS) and not (tokens & _INSTITUTION_WORDS)


def _is_institution(part: str) -> bool:
    return bool(_tokens_of(part) & _INSTITUTION_WORDS)


def _is_place(part: str) -> bool:
    """Whether a fragment reads as a location rather than a school or degree.

    Only recognised places count. Guessing from shape — short and capitalised —
    classified "MIT" as a city and left the degree with no school attached.
    """
    from jobhunt.adapters.filters import ANYWHERE, country_of

    tokens = _tokens_of(part)
    if tokens & (_DEGREE_WORDS | _INSTITUTION_WORDS):
        return False
    return bool(country_of(part)) or bool(tokens & ANYWHERE)


def _blank_education(school: str = "", degree: str = "") -> dict[str, Any]:
    return {"school": school, "degree": degree, "field": "",
            "start": "", "end": "", "location": ""}


def _parse_projects_block(lines: list[str]) -> list[dict[str, Any]]:
    """Parse a projects section into named projects with descriptions.

    Only a *short* line starts a new project. Every non-bullet line used to, so
    each wrapped description line became a phantom project — four projects came
    out as eight, half of them fragments like "services" and "libraries for
    dynamic monitoring and alerts.". Continuation lines now extend the
    description they belong to.
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        bullet_m = _BULLET_PREFIX_RE.match(line)
        if bullet_m and current is not None:
            current["bullets"].append(line[bullet_m.end():].strip())
            continue
        header = line.strip()
        if not header:
            continue
        link_m = _LINK_RE.search(header)
        link = link_m.group(0).rstrip(".,") if link_m else ""
        body = (_LINK_RE.sub("", header).strip(" |,–—-·\t") if link else header)

        # A project name is short and label-like. Prose — long, or ending in a
        # full stop — describes the project above it.
        # "Name  |  Kafka, PySpark, Docker": the part after a bar is the
        # project's stack, not part of its name.
        stack: list[str] = []
        if "|" in body:
            body, _, stack_text = body.partition("|")
            body = body.strip()
            stack = [t.strip().lower() for t in re.split(r"[,;/]", stack_text) if t.strip()]
        pieces = re.split(r"\s*[—–]\s*|\s+-\s+", body, maxsplit=1)
        name_part = pieces[0].strip()
        tail = pieces[1].strip() if len(pieces) > 1 else ""
        starts_project = (
            current is None
            or (len(name_part) <= 60 and not name_part.endswith(".") and bool(tail or len(body) <= 60))
        )
        if starts_project:
            if current is not None:
                entries.append(current)
            current = {"name": name_part, "description": tail, "bullets": [],
                       "link": link, "skills": stack}
        else:
            current["description"] = f"{current['description']} {body}".strip()
            current["link"] = current["link"] or link
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
    out["section_skills"] = _parse_skills_block(sections.get("skills") or [])
    out["achievements"] = [
        _BULLET_PREFIX_RE.sub("", ln).strip() for ln in sections.get("achievements") or []
        if _BULLET_PREFIX_RE.sub("", ln).strip()
    ]
    return out


def _parse_skills_block(lines: list[str]) -> list[str]:
    """Skills named in the résumé's own skills section.

    The closed 130-word vocabulary this module matches against is what the whole
    document is scanned with, and it silently drops anything not on the list —
    a PySpark/Databricks/ETL/Next.js/Prisma résumé came back with none of them.
    The skills *section* is the one place a candidate lists their skills
    deliberately, so it is read as an open vocabulary: strip a leading category
    label ("Languages:", "DevOps & Tools:") and split the rest on commas.
    """
    out: list[str] = []
    for line in lines:
        clean = _BULLET_PREFIX_RE.sub("", line).strip()
        if not clean:
            continue
        # "Languages: Python, SQL" → keep only what follows the label. A colon
        # late in a long line is prose, not a label.
        if ":" in clean and clean.index(":") <= 40:
            clean = clean.split(":", 1)[1]
        for part in re.split(r"[,;|/]|\s{2,}|•", clean):
            skill = part.strip(" .•-–—")
            # Skip prose: real skill names are short and few-worded.
            if skill and len(skill) <= 32 and len(skill.split()) <= 4:
                out.append(skill.lower())
    # Preserve order, drop repeats.
    return list(dict.fromkeys(out))


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
        # Table cells are not in ``doc.paragraphs``, and a two-column résumé is
        # almost always laid out as a table — so paragraph-only extraction
        # returned close to nothing while still reporting success.
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    parts.extend(p.text for p in cell.paragraphs)
        return "\n".join(parts)
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


def _clean_skills(skills: list[str]) -> list[str]:
    from jobhunt.skill_names import key

    return list(dict.fromkeys(s.strip().lower() for s in skills
                              if s.strip() and key(s)))


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
        skills=_clean_skills(form.get("skills", [])),
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


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,11}\d")
# Template blanks left in a résumé: "[Company Name]", "[Email]", "<Phone>".
_PLACEHOLDER_RE = re.compile(r"[\[<]\s*([A-Za-z][A-Za-z ./&-]{1,30}?)\s*[\]>]")


def contact_from_text(text: str) -> dict[str, str]:
    """Name, email and phone from résumé text; the section parser skips the
    header they live in."""
    email = _EMAIL_RE.search(text)
    phone = _PHONE_RE.search(text)
    name = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,60}", line) and len(line.split()) <= 5:
            name = line.title() if line.isupper() else line
        break
    return {
        "name": name,
        "email": email.group().lower() if email else "",
        "phone": " ".join(phone.group().split()) if phone else "",
    }


def scrub_placeholders(result: dict[str, Any]) -> list[str]:
    """Blank template placeholders out of a parse result, and name them.

    A résumé still holding "[Company Name]" would put exactly that into every
    tailored résumé and application form.
    """
    found: list[str] = []

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            for m in _PLACEHOLDER_RE.finditer(value):
                found.append(m.group(1).strip())
            return " ".join(_PLACEHOLDER_RE.sub("", value).split()).strip(" |,–—-")
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value

    for key in ("experiences", "education", "projects", "contact", "links"):
        if key in result:
            result[key] = clean(result[key])
    return list(dict.fromkeys(found))
