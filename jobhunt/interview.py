"""AI interview prep — question generation + answer feedback.

Both entry points work fully offline with ``llm=None`` (deterministic
templates / heuristics) and accept an optional best-effort LLM callback that
follows the same ``(action: str, payload: dict) -> str`` shape used elsewhere
in the package (see ``jobhunt/llm/callbacks.py``). Any LLM failure — bad
response, missing network, raised exception — falls back to the
deterministic path so this module never raises on the caller's behalf.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from jobhunt.agents.resume import _best_keywords

logger = logging.getLogger(__name__)

LLMCallback = Callable[[str, dict], str]

# Generic STAR-style behavioral prompts — relevant to virtually any role, so
# they need no JD context to be useful.
_BEHAVIORAL_QUESTIONS = [
    "Tell me about a time you had to meet a tight deadline. What was the "
    "situation and how did you handle it?",
    "Describe a conflict you had with a teammate or manager. How was it "
    "resolved?",
    "Walk me through a project that didn't go as planned. What did you "
    "learn from it?",
    "Tell me about a time you had to influence a decision without having "
    "direct authority.",
    "Describe a situation where you had to learn something new very "
    "quickly to get a job done.",
    "Tell me about a time you received critical feedback. How did you "
    "respond?",
    "Give an example of when you went above and beyond what was asked of "
    "you.",
    "Describe a time you had to prioritize among several competing tasks. "
    "How did you decide?",
]

_TECHNICAL_TEMPLATES = [
    "Walk me through how you'd use {kw} to solve a real-world problem "
    "you've faced.",
    "What trade-offs would you consider when introducing {kw} into an "
    "existing system?",
    "How would you debug a production issue involving {kw}?",
    "Explain {kw} to someone unfamiliar with it, then describe a project "
    "where you applied it.",
    "What's a mistake you've seen teams make with {kw}, and how would you "
    "avoid it?",
]

_DEFAULT_TECHNICAL_QUESTION = (
    "Walk me through a technical project you're proud of, focusing on the "
    "decisions you made and why."
)


def _technical_keywords(job: dict, doc: dict | None, limit: int) -> list[str]:
    """Best-effort technical keyword list for a posting.

    Prefers ``_best_keywords`` on the JD text (richest signal); falls back to
    the tailored document's matched/missing keywords when there's no JD text
    (e.g. a dashboard ``job`` dict, which doesn't carry ``jd_text``).
    """
    jd_text = str(job.get("jd_text", "") or "")
    if jd_text.strip():
        kws = _best_keywords(jd_text, limit)
        if kws:
            return kws
    doc = doc or {}
    ordered: list[str] = []
    seen: set[str] = set()
    for kw in list(doc.get("matched_keywords", [])) + list(doc.get("missing_keywords", [])):
        k = str(kw).lower()
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
        if len(ordered) >= limit:
            break
    return ordered


def _deterministic_technical_questions(
    job: dict, doc: dict | None, count: int,
) -> list[dict]:
    kws = _technical_keywords(job, doc, max(count, 1))
    if not kws:
        return [{"type": "technical", "question": _DEFAULT_TECHNICAL_QUESTION}]
    questions = []
    for i, kw in enumerate(kws[:count]):
        template = _TECHNICAL_TEMPLATES[i % len(_TECHNICAL_TEMPLATES)]
        questions.append({"type": "technical", "question": template.format(kw=kw)})
    return questions


def _deterministic_behavioral_questions(count: int) -> list[dict]:
    return [
        {"type": "behavioral", "question": q}
        for q in _BEHAVIORAL_QUESTIONS[:count]
    ]


def _parse_llm_questions(raw: str) -> list[str]:
    """Split newline-separated LLM output into clean question strings."""
    out = []
    for line in str(raw or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if line:
            out.append(line)
    return out


def generate_questions(
    profile: Any,
    job: dict,
    doc: dict | None = None,
    llm: LLMCallback | None = None,
) -> list[dict]:
    """Produce ~8 interview questions mixing behavioral + technical.

    Each item is ``{"type": "behavioral" | "technical", "question": str}``.
    Works fully with ``llm=None`` (deterministic templates). When ``llm`` is
    given, it's called best-effort with action ``"interview_questions"``;
    on any exception (or empty/unparseable output) this falls back to the
    deterministic templates so the function never raises.
    """
    n_behavioral, n_technical = 4, 4

    if llm is not None:
        try:
            raw = llm("interview_questions", {
                "profile": getattr(profile, "to_dict", lambda: {})(),
                "job": job,
                "doc": doc or {},
            })
            lines = _parse_llm_questions(raw)
            if lines:
                questions = []
                kws = set(_technical_keywords(job, doc, 20))
                for line in lines:
                    is_technical = any(kw in line.lower() for kw in kws)
                    questions.append({
                        "type": "technical" if is_technical else "behavioral",
                        "question": line,
                    })
                return questions
        except Exception:
            logger.debug("LLM question generation failed, using templates", exc_info=True)

    behavioral = _deterministic_behavioral_questions(n_behavioral)
    technical = _deterministic_technical_questions(job, doc, n_technical)
    return behavioral + technical


# --------------------------------------------------------------------- feedback

_FILLER_WORDS = {
    "um", "uh", "like", "basically", "actually", "literally", "stuff", "things",
}


def _heuristic_feedback(question: str, answer: str) -> dict:
    words = answer.split()
    n_words = len(words)
    lower = answer.lower()

    # Structure: STAR-style answers mention situation/task/action/result-ish
    # signal words and have enough length to actually tell a story.
    star_signals = ["situation", "task", "action", "result", "first", "then",
                     "so i", "as a result", "led to", "ultimately"]
    star_hits = sum(1 for s in star_signals if s in lower)
    length_score = min(1.0, n_words / 120)
    structure = round(min(1.0, 0.4 * length_score + 0.15 * star_hits), 3)

    # Relevance: how much of the question's meaningful vocabulary reappears
    # in the answer.
    q_tokens = {w.strip(".,?!").lower() for w in question.split() if len(w) > 3}
    a_tokens = {w.strip(".,?!").lower() for w in answer.split()}
    overlap = len(q_tokens & a_tokens)
    relevance = round(min(1.0, overlap / max(1, len(q_tokens)) * 1.5), 3)

    # Specificity: numbers/percentages and concrete nouns signal a real,
    # quantified example rather than a vague generality.
    has_number = any(ch.isdigit() for ch in answer)
    specific_signals = ["%", "percent", "users", "team", "project", "metric",
                         "reduced", "increased", "improved", "built", "shipped"]
    spec_hits = sum(1 for s in specific_signals if s in lower)
    specificity = round(min(1.0, (0.3 if has_number else 0.0) + 0.12 * spec_hits), 3)

    filler_hits = sum(1 for w in words if w.strip(".,?!").lower() in _FILLER_WORDS)

    tips: list[str] = []
    if n_words < 40:
        tips.append("Add more detail — aim for a few sentences that tell a full story.")
    if structure < 0.5:
        tips.append("Structure your answer with Situation, Task, Action, and Result (STAR).")
    if relevance < 0.3:
        tips.append("Tie your answer more directly back to the specific question asked.")
    if specificity < 0.3:
        tips.append("Add concrete numbers or outcomes (e.g. % improvement, team size, scope).")
    if filler_hits:
        tips.append("Trim filler words to sound more confident and concise.")
    if not tips:
        tips.append("Solid answer — consider quantifying the impact even further.")

    overall = round((structure + relevance + specificity) / 3, 3)
    return {
        "scores": {
            "structure": structure,
            "relevance": relevance,
            "specificity": specificity,
        },
        "tips": tips,
        "overall": overall,
    }


def _coerce_feedback(data: dict) -> dict | None:
    try:
        scores = data["scores"]
        out = {
            "scores": {
                "structure": float(scores["structure"]),
                "relevance": float(scores["relevance"]),
                "specificity": float(scores["specificity"]),
            },
            "tips": [str(t) for t in data.get("tips", [])],
            "overall": float(data["overall"]),
        }
        return out
    except (KeyError, TypeError, ValueError):
        return None


def answer_feedback(
    question: str, answer: str, llm: LLMCallback | None = None,
) -> dict:
    """Score a candidate's interview answer.

    Returns ``{"scores": {"structure", "relevance", "specificity"}, "tips":
    [...], "overall": float}`` with every score in ``0..1``. Deterministic
    heuristic fallback when ``llm`` is ``None``; otherwise best-effort calls
    ``llm("interview_feedback", {...})`` expecting a JSON-shaped dict (or a
    JSON string) matching that schema, falling back to the heuristic on any
    exception or malformed output.
    """
    if llm is not None:
        try:
            raw = llm("interview_feedback", {"question": question, "answer": answer})
            data = raw
            if isinstance(raw, str):
                import json
                data = json.loads(raw)
            if isinstance(data, dict):
                coerced = _coerce_feedback(data)
                if coerced is not None:
                    return coerced
        except Exception:
            logger.debug("LLM feedback generation failed, using heuristic", exc_info=True)

    return _heuristic_feedback(question, answer)
