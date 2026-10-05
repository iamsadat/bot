"""Callback factories that adapt ``LLMClient`` to the hooks expected by
``resume_template.build_resume_draft`` and by the peer-critique helper.
"""

from __future__ import annotations

import json
from typing import Callable

from jobhunt.llm.anthropic_client import LLMClient
from jobhunt.llm.grounding import (
    clean_llm_text,
    grounding_violations,
    profile_fact_sheet,
    word_count,
)


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

        if action == "cover_letter":
            return _cover_letter(client, payload, model)
        if action == "interview_questions":
            return _interview_questions(client, payload, model)
        if action == "interview_feedback":
            return _interview_feedback(client, payload, model)
        if action == "outreach":
            return _outreach(client, payload, model)

        # Unknown action — let the caller fall back to deterministic phrasing.
        return ""

    return _callback


# ------------------------------------------------------- grounded generation
#
# Cover letter, interview prep and outreach. Each builder sends only the
# PII-free fact sheet (``grounding.profile_fact_sheet``) plus the job context,
# then post-checks the output; any violation returns "" so the caller keeps
# its deterministic text.

_GROUNDING_RULES = """\
Grounding rules (mandatory):
- Use ONLY facts stated in CANDIDATE FACTS and JOB below.
- Never invent employers, job titles, projects, metrics, numbers, dates,
  degrees, certifications or skills. If a fact is not listed, leave it out.
- Do not claim the candidate has a skill the JOB asks for unless it also
  appears in CANDIDATE FACTS."""

_COVER_SYSTEM = f"""\
You write cover letters for job applications.
{_GROUNDING_RULES}
Style:
- 150-220 words, 3 short paragraphs, plain text, no markdown, no placeholders.
- Specific to this role and company: connect 2-3 of the candidate's real
  experiences or skills to what the JOB asks for.
- Professional, courteous and neutral in tone, suitable for the Indian job
  market: no slang, no exaggeration, no salary or notice-period talk.
- Open with "Dear Hiring Team at <company>," and close with "Regards," and
  the candidate's name on the next line.
Return only the letter."""

_QUESTIONS_SYSTEM = f"""\
You prepare a candidate for a job interview by writing likely questions.
{_GROUNDING_RULES}
Write exactly 8 questions, one per line, each starting with a tag:
- 3 lines "[technical] ..." on skills or responsibilities named in the JOB.
- 3 lines "[behavioral] ..." STAR-style questions relevant to the role.
- 2 lines "[resume] ..." probing a specific experience or project listed in
  CANDIDATE FACTS (name it as written there).
No numbering, no preamble, no answers."""

_FEEDBACK_SYSTEM = """\
You are an interview coach scoring a candidate's practice answer.
Judge ONLY the answer text given; do not assume facts it does not state.
Rubric (each a float from 0 to 1):
- structure: STAR completeness (Situation, Task, Action, Result all present
  and in a clear order).
- specificity: concrete details, the candidate's own actions, measurable
  outcomes.
- relevance: how directly the answer addresses the question asked.
overall is the mean of the three. tips are 2-4 short, actionable fixes that
refer to what the answer actually says or lacks.
Return strict JSON only, no markdown:
{"scores": {"structure": 0.0, "relevance": 0.0, "specificity": 0.0},
 "tips": ["..."], "overall": 0.0}"""

_OUTREACH_SYSTEM = f"""\
You write short recruiter outreach notes (LinkedIn message or email body).
{_GROUNDING_RULES}
Style:
- At most 100 words, plain text, no subject line, no markdown, no placeholders.
- Greet the contact by first name, mention the exact role and company, give
  one or two real reasons the candidate fits, and ask for a short chat.
- Polite, professional and neutral; sign off with the candidate's name.
Return only the message."""

_JD_CHARS = 6000


def _job_block(title: str, company: str, jd_text: str = "",
               keywords: list | None = None) -> str:
    lines = [f"Role: {title}", f"Company: {company}"]
    if keywords:
        lines.append("Key skills from the job: " + ", ".join(map(str, keywords)))
    if jd_text:
        lines.append("Job description:\n" + jd_text[:_JD_CHARS])
    return "\n".join(lines)


def _cover_letter(client: LLMClient, payload: dict, model: str | None) -> str:
    facts = profile_fact_sheet(payload.get("profile"))
    title = str(payload.get("posting_title", ""))
    company = str(payload.get("posting_company", ""))
    jd_text = str(payload.get("jd_text", "") or "")
    matched = list(payload.get("matched_keywords") or [])
    job = _job_block(title, company, jd_text)
    user = (f"CANDIDATE FACTS:\n{facts}\n\nJOB:\n{job}\n\n"
            "Skills both the candidate and the job share: "
            f"{', '.join(matched) or 'none listed'}")
    text = clean_llm_text(client.complete(_COVER_SYSTEM, user, max_tokens=600, model=model))
    if not 110 <= word_count(text) <= 280:
        return ""
    if grounding_violations(text, [facts, title, company, jd_text, ", ".join(map(str, matched))]):
        return ""
    return text


def _interview_questions(client: LLMClient, payload: dict, model: str | None) -> str:
    facts = profile_fact_sheet(payload.get("profile"))
    job = payload.get("job") or {}
    doc = payload.get("doc") or {}
    title = str(job.get("title") or doc.get("title") or "")
    company = str(job.get("company") or doc.get("company") or "")
    jd_text = str(job.get("jd_text", "") or "")
    keywords = (list(doc.get("matched_keywords") or [])
                + list(doc.get("missing_keywords") or []))
    job_text = _job_block(title, company, jd_text, keywords)
    user = f"CANDIDATE FACTS:\n{facts}\n\nJOB:\n{job_text}"
    text = clean_llm_text(client.complete(_QUESTIONS_SYSTEM, user, max_tokens=700, model=model))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().endswith("?")]
    if len(lines) < 4:
        return ""
    if grounding_violations("\n".join(lines), [facts, job_text], check_numbers=False):
        return ""
    return "\n".join(lines)


def _interview_feedback(client: LLMClient, payload: dict, model: str | None) -> str:
    question = str(payload.get("question", ""))
    answer = str(payload.get("answer", ""))
    user = f"QUESTION:\n{question}\n\nANSWER:\n{answer}"
    text = clean_llm_text(client.complete(_FEEDBACK_SYSTEM, user, max_tokens=500, model=model))
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ""
    return json.dumps(data) if isinstance(data, dict) else ""


def _outreach(client: LLMClient, payload: dict, model: str | None) -> str:
    facts = profile_fact_sheet(payload.get("profile"))
    title = str(payload.get("title", ""))
    company = str(payload.get("company", ""))
    contact = str(payload.get("contact", "") or "")
    first = contact.split()[0] if contact.strip() else "there"
    jd_text = str(payload.get("jd_text", "") or "")
    strengths = list(payload.get("strengths") or [])
    job = _job_block(title, company, jd_text)
    user = (f"CANDIDATE FACTS:\n{facts}\n\nJOB:\n{job}\n\n"
            f"Contact first name: {first}\n"
            "Candidate's strongest matches for this role: "
            f"{', '.join(strengths) or 'none listed'}")
    text = clean_llm_text(client.complete(_OUTREACH_SYSTEM, user, max_tokens=300, model=model))
    if not text or word_count(text) > 120:
        return ""
    if grounding_violations(text, [facts, title, company, jd_text, first,
                                   ", ".join(map(str, strengths))]):
        return ""
    return text


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
