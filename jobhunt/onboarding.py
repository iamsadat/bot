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

    return {
        "skills": skills,
        "inferred_titles": titles[:4],
        "experience_years": experience_years,
    }


def build_user_profile(form: dict[str, Any]) -> UserProfile:
    """Construct a UserProfile dataclass from validated onboarding form data."""
    return UserProfile(
        user_id=uuid.uuid4().hex,
        name=form["name"].strip(),
        email=form["email"].strip().lower(),
        target_roles=[r.strip() for r in form.get("target_roles", []) if r.strip()],
        locations=[loc.strip() for loc in form.get("locations", []) if loc.strip()],
        min_salary=form.get("min_salary") or None,
        remote_ok=form.get("remote_ok", True),
        skills=[s.strip().lower() for s in form.get("skills", []) if s.strip()],
        culture_keywords=[c.strip() for c in form.get("culture_keywords", []) if c.strip()],
        experiences=form.get("experiences", []),
        veto_companies=[v.strip() for v in form.get("veto_companies", []) if v.strip()],
        weekly_target=int(form.get("weekly_target", 10)),
    )
