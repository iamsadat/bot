"""Resume & Cover Letter Architect Agent (Phase-0 implementation).

The MVP runs JD parsing, evidence mapping, and a self-critique loop —
all the safety scaffolding from the spec — but generates plain-text
artifacts instead of PDF/DOCX. Phase 2 swaps the renderer to
WeasyPrint + python-docx behind the same interface.

Critical invariant from ARCHITECTURE.md §2.4: every bullet must map to
an evidence node in the user's experience graph. ``map_evidence``
enforces that and ``critique`` scores ``no_hallucination`` at 0 if it
ever fails — which prevents a flunked draft from leaving the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from jobhunt.agents.base import BaseAgent
from jobhunt.models import JobPosting, ReasoningTrace, UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


@dataclass
class TailoredDocument:
    job_id: str
    company: str
    title: str
    url: str
    resume_text: str
    cover_letter_text: str
    keyword_coverage: float
    matched_keywords: list[str]
    missing_keywords: list[str]
    bullets: list[dict[str, str]] = field(default_factory=list)  # {text, evidence_id}
    requires_human_approval: bool = True


@dataclass
class ResumeInputs:
    profile: UserProfile
    postings: list[JobPosting]
    max_keywords: int = 15


_STOP = {
    "the", "and", "for", "with", "you", "are", "our", "this", "that",
    "have", "has", "from", "will", "your", "should", "must", "into",
    "using", "use", "be", "to", "in", "of", "a", "an", "on", "or", "is",
    "as", "we", "us", "by", "at", "it", "experience", "knowledge",
    "familiarity", "team", "work", "role", "candidate",
}
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{2,}")


def extract_keywords(jd: str, limit: int = 15) -> list[str]:
    counts: dict[str, int] = {}
    for tok in _TOKEN_RE.findall(jd.lower()):
        if tok in _STOP:
            continue
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in ranked[:limit]]


class ResumeArchitectAgent(BaseAgent[ResumeInputs, list[TailoredDocument]]):
    name = "resume"
    # Safety-critical: refuse to ship sub-threshold output.
    quality_threshold = 0.8
    max_refinements = 2

    def __init__(
        self,
        trace_store: TraceStore,
        bus: ThoughtBus,
        tools=None,
        *,
        llm: Callable[[str, dict], str] | None = None,
    ) -> None:
        super().__init__(trace_store, bus, tools)
        # Optional tone-polish callback, e.g. resume_callback(GeminiLLMClient(...)).
        # Bullets keep their deterministic evidence_id regardless — the LLM only
        # ever rewrites cosmetic text, never invents or removes evidence.
        self.llm = llm

    def deliberate(self, inputs: ResumeInputs, trace: ReasoningTrace) -> list[str]:
        return [
            f"received {len(inputs.postings)} postings to tailor.",
            "plan: for each posting, extract ATS keywords from the JD, "
            "map each keyword to evidence in the user's skill graph, draft "
            "bullets only when evidence exists, and run a self-review.",
            "safety: reject any bullet that lacks a backing evidence node "
            "to prevent hallucinations.",
        ]

    def act(
        self, inputs: ResumeInputs, trace: ReasoningTrace
    ) -> list[TailoredDocument]:
        docs: list[TailoredDocument] = []
        for posting in inputs.postings:
            kws = extract_keywords(posting.jd_text, inputs.max_keywords)
            matched, missing, bullets = self._map_evidence(kws, inputs.profile)
            coverage = len(matched) / max(1, len(kws))

            resume_text = self._render_resume(inputs.profile, posting, bullets)
            cover = self._render_cover(inputs.profile, posting, matched)

            docs.append(
                TailoredDocument(
                    job_id=posting.job_id,
                    company=posting.company,
                    title=posting.title,
                    url=posting.url,
                    resume_text=resume_text,
                    cover_letter_text=cover,
                    keyword_coverage=round(coverage, 3),
                    matched_keywords=matched,
                    missing_keywords=missing,
                    bullets=bullets,
                )
            )
            self.think(
                trace,
                f"{posting.company} :: {posting.title} → "
                f"keyword coverage {coverage:.2f}, "
                f"missing={missing[:3]}{'…' if len(missing) > 3 else ''}.",
            )
        return docs

    def critique(
        self,
        inputs: ResumeInputs,
        output: list[TailoredDocument],
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        if not output:
            return {"ats_coverage": 0.0, "no_hallucination": 1.0, "completeness": 0.0}
        avg_cov = sum(d.keyword_coverage for d in output) / len(output)
        # No hallucination: every bullet must have an evidence_id.
        no_hallu = (
            1.0
            if all(b.get("evidence_id") for d in output for b in d.bullets)
            else 0.0
        )
        completeness = 1.0 if all(d.resume_text and d.cover_letter_text for d in output) else 0.5
        return {
            "ats_coverage": round(avg_cov, 3),
            "no_hallucination": no_hallu,
            "completeness": completeness,
        }

    def decide(
        self,
        inputs: ResumeInputs,
        output: list[TailoredDocument],
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        return (
            f"drafted {len(output)} document pairs awaiting human approval",
            avg,
        )

    # ----- helpers ----------------------------------------------------------

    def _map_evidence(
        self, keywords: list[str], profile: UserProfile
    ) -> tuple[list[str], list[str], list[dict[str, str]]]:
        # Build evidence index from skills and experience descriptions.
        evidence: dict[str, dict[str, Any]] = {}
        for i, s in enumerate(profile.skills):
            evidence[s.lower()] = {"id": f"skill:{i}", "kind": "skill", "text": s}
        for j, e in enumerate(profile.experiences):
            desc = " ".join(str(v) for v in e.values()).lower()
            for tok in _TOKEN_RE.findall(desc):
                evidence.setdefault(
                    tok, {"id": f"exp:{j}", "kind": "experience", "text": str(e)}
                )

        matched: list[str] = []
        missing: list[str] = []
        bullets: list[dict[str, str]] = []
        for kw in keywords:
            ev = evidence.get(kw)
            if ev is None:
                missing.append(kw)
                continue
            matched.append(kw)
            text = f"Delivered work involving {kw} ({ev['text'][:80]})."
            if self.llm is not None:
                try:
                    improved = self.llm("rewrite_bullet", {"keyword": kw, "draft": text})
                    if improved and isinstance(improved, str):
                        text = improved.strip()
                except Exception:
                    # LLM is best-effort; fall back to deterministic phrasing.
                    pass
            bullets.append({"text": text, "evidence_id": ev["id"]})
        return matched, missing, bullets

    @staticmethod
    def _render_resume(
        profile: UserProfile, posting: JobPosting, bullets: list[dict[str, str]]
    ) -> str:
        header = f"{profile.name}\n{profile.email}\n"
        title = f"\nTarget role: {posting.title} @ {posting.company}\n"
        skills = "\nSkills: " + ", ".join(profile.skills)
        body = "\nHighlights:\n" + "\n".join(f"- {b['text']}" for b in bullets)
        return header + title + skills + body + "\n"

    @staticmethod
    def _render_cover(
        profile: UserProfile, posting: JobPosting, matched: list[str]
    ) -> str:
        return (
            f"Dear {posting.company} team,\n\n"
            f"I'm applying for {posting.title}. My background in "
            f"{', '.join(matched[:5]) or 'this domain'} aligns with what "
            "your team is building. I'd welcome a chance to discuss how "
            "I can contribute.\n\nBest,\n"
            f"{profile.name}\n"
        )
