"""Grounding helpers for LLM-written candidate text.

Two jobs:

* ``profile_fact_sheet`` turns a profile dict into the compact, PII-free list
  of facts the model is allowed to use. Contact details, links, salary and
  ATS screening answers are never included; whatever text remains is passed
  through the same ``_redact_pii`` the clients apply.
* ``grounding_violations`` is the cheap post-check. It flags any known skill
  (``skills_taxonomy`` vocabulary) or any number the output names that does
  not appear in the supplied sources (profile facts + job description). A
  non-empty result means the model invented something and the caller must
  fall back to its deterministic text.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from jobhunt.llm.anthropic_client import _redact_pii

# Profile keys that hold contact details, money or internal state — never sent.
_EXCLUDED_KEYS = {
    "user_id", "email", "phone", "links", "link", "url", "min_salary",
    "current_salary", "application_answers", "veto_companies",
    "weekly_target", "auto_apply", "daily_apply_cap", "relevance_floor",
    "radar_enabled", "radar_keywords", "remote_ok", "locations",
    "culture_keywords", "seniority_level",
}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")

# Taxonomy terms that are also everyday prose ("I wish to express my
# interest", "distributed teams"). Not checked in model output — they would
# reject honest letters far more often than they would catch an invented skill.
_PROSE_TERMS = frozenset({
    "express", "swift", "unity", "notion", "shell", "lambda", "node",
    "distributed", "containers", "pipelines", "orchestration", "encryption",
    "warehousing", "transformers", "apollo", "agile",
})


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if k in _EXCLUDED_KEYS or v in (None, "", [], {}):
                continue
            parts.append(f"{k}: {_flatten(v)}")
        return "; ".join(parts)
    if isinstance(value, (list, tuple)):
        return ", ".join(_flatten(v) for v in value if v not in (None, "", [], {}))
    return " ".join(str(value).split())


def profile_fact_sheet(profile: dict | None, *, max_chars: int = 4000) -> str:
    """The candidate facts an LLM may draw on, one section per line."""
    profile = profile or {}
    lines: list[str] = []
    if profile.get("name"):
        lines.append(f"Name: {profile['name']}")
    if profile.get("current_title"):
        lines.append(f"Current title: {profile['current_title']}")
    if profile.get("experience_years") is not None:
        lines.append(f"Years of experience: {profile['experience_years']}")
    if profile.get("target_roles"):
        lines.append(f"Target roles: {_flatten(profile['target_roles'])}")
    if profile.get("skills"):
        lines.append(f"Skills: {_flatten(profile['skills'])}")
    for label, key in (("Experience", "experiences"), ("Project", "projects"),
                       ("Education", "education")):
        for item in profile.get(key) or []:
            text = _flatten(item)
            if text:
                lines.append(f"{label}: {text}")
    sheet = "\n".join(lines) or "(no profile details provided)"
    return _redact_pii(sheet)[:max_chars]


def clean_llm_text(raw: Any) -> str:
    """Strip whitespace, markdown code fences and wrapping quotes."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def word_count(text: str) -> int:
    return len(text.split())


def _skill_terms(text: str) -> set[str]:
    """Known skill terms named in ``text``, as written (not canonicalised).

    Mirrors ``skills_taxonomy.skills_in_text`` but keeps the surface term, so
    "django" is not excused by the profile saying "python" (they share a
    taxonomy group, but one does not imply the other).
    """
    from jobhunt.skills_taxonomy import KNOWN_SKILLS, _AMBIGUOUS, _COMPOUND, _tokens

    if not text:
        return set()
    tokens = _tokens(text)
    found = {t for t in KNOWN_SKILLS if t in tokens and t not in _AMBIGUOUS}
    for term, pattern in _AMBIGUOUS.items():
        if term in KNOWN_SKILLS and re.search(pattern, text):
            found.add(term)
    flat = " ".join(text.lower().replace("-", " ").replace("/", " ").split())
    for term in _COMPOUND:
        if term.replace("-", " ") in flat:
            found.add(term)
    return found


def grounding_violations(
    text: str, sources: Iterable[str], *, check_numbers: bool = True,
) -> list[str]:
    """What ``text`` claims that none of ``sources`` supports.

    Returns entries like ``"skill:django"`` or ``"number:40"``; empty when the
    output is grounded. A skill term is supported when the sources name it, or
    when it is the canonical name of a term they name ("kubernetes" for a
    profile that says "k8s", "python" for one that says "fastapi").
    """
    from jobhunt.skills_taxonomy import canonical

    source_text = "\n".join(s for s in sources if s)
    source_terms = _skill_terms(source_text)
    allowed = source_terms | {canonical(t) for t in source_terms}
    claimed = _skill_terms(text) - _PROSE_TERMS
    out = [f"skill:{s}" for s in sorted(claimed - allowed)]
    if check_numbers:
        source_numbers = set(_NUMBER_RE.findall(source_text))
        for num in _NUMBER_RE.findall(text):
            if num not in source_numbers:
                out.append(f"number:{num}")
    return out
