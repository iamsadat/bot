"""Callback factories that adapt ``LLMClient`` to the hooks expected by
``resume_template.build_resume_draft`` and by the peer-critique helper.
"""

from __future__ import annotations

import json
from typing import Callable

from jobhunt.llm.anthropic_client import LLMClient


# ------------------------------------------------------------------ resume


_BULLET_SYSTEM = """\
You rewrite resume bullets for ATS clarity and impact.
Rules:
- One sentence, ≤ 22 words.
- Keep every factual claim from the draft — do NOT invent metrics or employers.
- Lead with a strong action verb; weave in the ATS keyword naturally.
- Tailor the emphasis to the target role/company when provided.
- Return only the rewritten bullet, no preamble, no surrounding quotes."""

_SUMMARY_SYSTEM = """\
You write two-sentence resume summaries in third person.
Rules:
- Sentence 1: candidate's role/specialty + top skills.
- Sentence 2: why they're a strong fit for the specific role and company.
- ATS keywords must appear naturally — do not invent experience.
- Return only the two sentences, no preamble."""

_CRITIQUE_SYSTEM = """\
You score a resume against a job description and return strict JSON.
Output format (no markdown, no commentary):
{"score": <float 0-1>, "flags": [<string>, ...], "suggestions": [<string>, ...]}
score: overall fit (0=poor, 1=excellent).
flags: short strings for problems found (e.g. "missing_keyword:python").
suggestions: actionable fixes the candidate can make."""


def resume_callback(
    client: LLMClient,
    *,
    model: str | None = None,
) -> Callable[[str, dict], str]:
    """Return a ``(action, payload) -> str`` callback for ``build_resume_draft``."""

    def _callback(action: str, payload: dict) -> str:
        if action == "rewrite_bullet":
            keyword = payload.get("keyword", "")
            draft = payload.get("draft", "")
            company = payload.get("company", "")
            title = payload.get("title", "")
            ctx = (
                f"\nTarget role: {title} at {company}"
                if (title or company) else ""
            )
            user = f"Keyword: {keyword}\nDraft bullet: {draft}{ctx}"
            return client.complete(_BULLET_SYSTEM, user, max_tokens=80, model=model)

        if action == "summary":
            profile = payload.get("profile", {})
            title = payload.get("posting_title", "")
            company = payload.get("posting_company", "")
            keywords = payload.get("keywords", [])
            name = profile.get("name", "")
            skills = profile.get("skills", [])
            user = (
                f"Candidate: {name}\n"
                f"Skills: {', '.join(skills[:8])}\n"
                f"Target role: {title} at {company}\n"
                f"ATS keywords: {', '.join(keywords[:10])}"
            )
            return client.complete(_SUMMARY_SYSTEM, user, max_tokens=128, model=model)

        # Unknown action — let the caller fall back to deterministic phrasing.
        return ""

    return _callback


# ------------------------------------------------------------------ critique


def critique_callback(
    client: LLMClient,
    *,
    model: str | None = None,
) -> Callable[[dict], dict]:
    """Return a function that LLM-scores a resume against a job description.

    Input dict keys:
        posting_jd       (str)  — full job description text
        resume_text      (str)  — plain-text resume
        required_keywords (list[str]) — ATS keywords to check

    Returns:
        {"score": float, "flags": list[str], "suggestions": list[str]}
    """

    def _callback(inputs: dict) -> dict:
        jd = inputs.get("posting_jd", "")
        resume = inputs.get("resume_text", "")
        keywords = inputs.get("required_keywords", [])

        user = (
            f"Job description:\n{jd}\n\n"
            f"Resume:\n{resume}\n\n"
            f"Required keywords: {', '.join(keywords)}"
        )
        raw = client.complete(_CRITIQUE_SYSTEM, user, max_tokens=256, model=model)

        try:
            data = json.loads(raw)
            return {
                "score": float(data.get("score", 0.5)),
                "flags": list(data.get("flags", [])),
                "suggestions": list(data.get("suggestions", [])),
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            return {
                "score": 0.5,
                "flags": ["llm_parse_error"],
                "suggestions": [],
            }

    return _callback
