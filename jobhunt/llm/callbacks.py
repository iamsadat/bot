"""Callback factories that adapt ``LLMClient`` to the hooks expected by
``resume_template.build_resume_draft`` and by the peer-critique helper.
"""

from __future__ import annotations

import json
import re
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
You lightly edit resume bullets so they read crisply to a recruiter.
Rules:
- Keep every fact, number, tool and scope from the draft. Add none.
- Keep roughly the same length. If the draft already reads well, return it
  unchanged.
- Start with a strong past-tense action verb. No first-person pronouns.
- Do not insert words from the target job title, and do not force in a
  keyword: use one only if the draft already describes that exact thing.
- Return only the bullet. No notes, no explanations, no quotes, no brackets."""

_BULLETS_SYSTEM = """\
You lightly edit the bullets of ONE resume entry so they read crisply to a
recruiter hiring for the target role.
Rules:
- Edit each bullet on its own; keep the same number of bullets, same order.
- Keep every fact, number, tool and scope. Add none, remove none.
- Keep each bullet roughly the same length. A bullet that already reads well
  stays unchanged.
- Each bullet starts with a strong past-tense action verb; vary the verbs
  across bullets. No first-person pronouns.
- Never repeat words from the target job title across bullets (no padding
  every line with "reliable" for a reliability role). Never force in a
  keyword: use a job keyword only where the bullet already describes that
  exact thing under another name.
- Never add notes, explanations, brackets or commentary.
Return strict JSON only: an array of strings, one per input bullet."""

_SUMMARY_SYSTEM = """\
You write the professional summary at the top of a resume.
Rules:
- 2 sentences, 35-60 words total, in implied first person: no name and no
  pronouns (no he/she/they/I/my/his/her). Start with the candidate's title
  without level words (no "Junior", "Associate", "Intern"), e.g.
  "Data Engineer with ...".
- Use ONLY the CANDIDATE FACTS. Never invent employers, numbers, years or
  skills. Name 3-5 of the candidate's real skills that the job also wants.
- Sentence 2 states one concrete result taken from the facts, exactly as
  stated there: never combine numbers from different facts into one claim.
- Never mention the target company, never say the candidate "is a fit", and
  never add notes or commentary.
Return only the summary."""

_CRITIQUE_SYSTEM = """\
You score a resume against a job description and return strict JSON.
Output format (no markdown, no commentary):
{"score": <float 0-1>, "flags": [<string>, ...], "suggestions": [<string>, ...]}
score: overall fit (0=poor, 1=excellent).
flags: short strings for problems found (e.g. "missing_keyword:python").
suggestions: actionable fixes the candidate can make."""


_LEVEL_WORDS = {"senior", "sr", "junior", "jr", "lead", "staff", "principal", "head",
                "associate", "intern", "trainee", "i", "ii", "iii", "iv", "mid", "level"}


def role_key(title: str) -> str:
    """A job title reduced to its role ("07.Data Engineer II" → "data engineer"),
    so cached edits are shared across postings for the same kind of job."""
    words = re.findall(r"[a-z]+", (title or "").lower())
    return " ".join(w for w in words if w not in _LEVEL_WORDS)


def resume_callback(
    client: LLMClient,
    *,
    model: str | None = None,
    light_model: str | None = None,
    cache=None,
) -> Callable[[str, dict], str]:
    """Return a ``(action, payload) -> str`` callback for ``build_resume_draft``.

    ``light_model`` (e.g. Haiku) runs the small, heavily guarded edits —
    bullets and summary; ``model`` the rest. Bullet and summary results are
    cached by their exact inputs (``jobhunt.llm.cache``), so the same résumé
    entry is not edited again for every posting of the same role.
    """
    from jobhunt.llm.cache import shared_cache, usage

    store = cache if cache is not None else shared_cache()
    if light_model is None and model is None:
        from jobhunt.llm.factory import light_model_for
        light_model = light_model_for(client)
    light = light_model or model

    def cached(key: str, produce: Callable[[], str]) -> str:
        hit = store.get(key)
        if hit is not None:
            usage.record(cached=True)
            return hit
        out = produce()
        store.put(key, out)
        return out

    def _callback(action: str, payload: dict) -> str:
        if action == "rewrite_bullet":
            draft = str(payload.get("draft", ""))
            keyword = payload.get("keyword", "")
            title = payload.get("title", "")
            lines = [f"Draft bullet: {draft}"]
            if keyword:
                lines.append(f"Job keyword (use only if it fits): {keyword}")
            if title:
                lines.append(f"Target role: {title}")
            text = clean_llm_text(client.complete(
                _BULLET_SYSTEM, "\n".join(lines), max_tokens=120, model=light))
            return text if bullet_ok(draft, text, title) else ""

        if action == "rewrite_bullets":
            drafts = [str(b) for b in payload.get("bullets") or []]
            key = store.key("bullets", drafts, role_key(str(payload.get("title", ""))))
            return cached(key, lambda: _rewrite_bullets(client, payload, light))

        if action == "summary":
            profile = payload.get("profile") or {}
            key = store.key("summary", profile_fact_sheet(
                {k: v for k, v in profile.items() if k != "name"}),
                role_key(str(payload.get("posting_title", ""))),
                sorted(str(k).lower() for k in (payload.get("keywords") or [])[:8]),
                str(payload.get("posting_company", "")).lower())
            return cached(key, lambda: _summary(client, payload, light))

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


# Commentary a model sometimes appends instead of following the rules — the
# "(Note: the keyword field was blank, ...)" that once shipped in a résumé.
_META_RE = re.compile(
    r"\b(?:note|keyword|ats|placeholder|as an ai|i (?:have|leaned|used|kept))\b|[\[\]{}]",
    re.I)
_PRONOUN_RE = re.compile(r"\b(?:he|she|his|her|him|they|their|i|my|me)\b", re.I)
_TITLE_STOP = {"engineer", "developer", "senior", "junior", "lead", "staff", "data",
               "software", "i", "ii", "iii", "the", "and", "of", "for"}


def _stems(text: str) -> set[str]:
    return {w.lower()[:6] for w in re.findall(r"[A-Za-z]{4,}", text)}


def bullet_ok(draft: str, text: str, title: str = "") -> bool:
    """Whether ``text`` is a faithful edit of the résumé bullet ``draft``."""
    if not text or "\n" in text.strip():
        return False
    if not 0.6 <= word_count(text) / max(word_count(draft), 1) <= 1.4:
        return False
    if _META_RE.search(text) and not _META_RE.search(draft):
        return False
    if _PRONOUN_RE.search(text) and not _PRONOUN_RE.search(draft):
        return False
    # Words of the job title the draft did not use are keyword padding.
    title_stems = {w.lower()[:6] for w in re.findall(r"[A-Za-z]{4,}", title)
                   if w.lower() not in _TITLE_STOP}
    if (title_stems & _stems(text)) - _stems(draft):
        return False
    return not grounding_violations(text, [draft])


def _rewrite_bullets(client: LLMClient, payload: dict, model: str | None) -> str:
    """One call per résumé entry, so the model sees its bullets side by side
    and varies them. Returns a JSON array; a bullet that fails ``bullet_ok``
    comes back as its original text."""
    drafts = [str(b) for b in payload.get("bullets") or []]
    if not drafts:
        return ""
    title = str(payload.get("title", ""))
    keywords = [str(k) for k in payload.get("keywords") or []][:12]
    user = (f"Target role: {title}\n"
            f"Job keywords (use only where a bullet already describes them): "
            f"{', '.join(keywords) or 'none'}\n"
            f"Bullets:\n{json.dumps(drafts, ensure_ascii=False)}")
    raw = clean_llm_text(client.complete(_BULLETS_SYSTEM, user,
                                         max_tokens=150 + 80 * len(drafts), model=model))
    try:
        edited = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(edited, list) or len(edited) != len(drafts):
        return ""
    out = [str(e).strip() if bullet_ok(d, str(e).strip(), title) else d
           for d, e in zip(drafts, edited)]
    return json.dumps(out, ensure_ascii=False)


def _summary(client: LLMClient, payload: dict, model: str | None) -> str:
    profile = payload.get("profile") or {}
    facts = profile_fact_sheet({k: v for k, v in profile.items() if k != "name"})
    title = str(payload.get("posting_title", ""))
    company = str(payload.get("posting_company", ""))
    keywords = [str(k) for k in payload.get("keywords") or []]
    user = (f"CANDIDATE FACTS:\n{facts}\n\nTarget role: {title}\n"
            f"Skills the job asks for: {', '.join(keywords[:12]) or 'none listed'}")
    text = clean_llm_text(client.complete(_SUMMARY_SYSTEM, user, max_tokens=200, model=model))
    text = re.sub(r"^(?:junior|jr\.?|associate|trainee)\s+", "", text, flags=re.I)
    text = text[:1].upper() + text[1:]
    if not 20 <= word_count(text) <= 75 or "\n" in text:
        return ""
    low = text.lower()
    name_parts = [p.lower() for p in str(profile.get("name", "")).split() if len(p) > 2]
    if (_PRONOUN_RE.search(text) or _META_RE.search(text)
            or (company and company.lower() in low)
            or any(re.search(rf"\b{re.escape(p)}\b", low) for p in name_parts)
            or re.search(r"\b(?:strong|great|ideal|perfect) fit\b", low)):
        return ""
    if grounding_violations(text, [facts]):
        return ""
    return text


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
- Call the employer only by the Company name given, never by another name
  the job description uses (a parent company, team or brand).
- No level words for the candidate's own title ("Junior", "Intern")
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

_JD_CHARS = 2500

# Where a job description says what it wants. Everything before it is
# usually the company pitch, which a letter has no use for.
_JD_FOCUS_RE = re.compile(
    r"(?:^|[.:!?]\s+)(?:requirements?|qualifications?|what you(?:'|’)?ll (?:do|bring|need)|"
    r"responsibilities|about the role|the role|you will|you have|must have)\b",
    re.I | re.M)


def focused_jd(jd_text: str, limit: int = _JD_CHARS) -> str:
    """The part of a job description that says what the job is and needs,
    capped at ``limit`` characters."""
    jd = " ".join((jd_text or "").split()) if "\n" not in (jd_text or "") else jd_text.strip()
    if len(jd) <= limit:
        return jd
    m = _JD_FOCUS_RE.search(jd)
    if m is None:
        return jd[:limit]
    start = m.start() + len(m.group(0)) - len(m.group(0).lstrip(".:!? \t\n"))
    # Near the end, step back so the cut still carries ``limit`` characters.
    start = min(start, max(0, len(jd) - limit))
    return jd[start:start + limit]


def _job_block(title: str, company: str, jd_text: str = "",
               keywords: list | None = None) -> str:
    lines = [f"Role: {title}", f"Company: {company}"]
    if keywords:
        lines.append("Key skills from the job: " + ", ".join(map(str, keywords)))
    if jd_text:
        lines.append("Job description:\n" + focused_jd(jd_text))
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
    # A question line ends in "?" or carries its tag: models often phrase a
    # prompt as "Walk me through …", which counting only "?" lines discarded.
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().endswith("?") or re.match(r"\s*\[(technical|behaviou?ral|resume)\]",
                                                     ln, re.I)]
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
