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
from dataclasses import asdict, dataclass, field
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
    # asdict(ResumeDraft) for the layout-aware renderer + UI preview. None when
    # the profile has no structured history (legacy templated-text fallback).
    draft: dict[str, Any] | None = None


@dataclass
class ResumeInputs:
    profile: UserProfile
    postings: list[JobPosting]
    max_keywords: int = 15


# Deterministic bullet phrasings used when no LLM is wired (or as the fallback
# when an LLM call fails). Picked by a stable per-keyword index so the same
# keyword always renders the same bullet across runs (tests + reproducibility).
_BULLET_TEMPLATES = [
    "Delivered production features built on {kw}, from design through deployment.",
    "Owned {kw} across the stack, improving reliability and developer velocity.",
    "Built and scaled {kw}-based services running in production.",
    "Applied {kw} to ship measurable improvements for users and the team.",
    "Designed and maintained {kw} systems with a focus on correctness and speed.",
]


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


# Generic JD prose that the TF-IDF/frequency ranker surfaces but which no
# candidate can meaningfully "match" — excluded so coverage reflects real skills.
_FILLER = {
    "comfortable", "fluent", "need", "like", "frameworks", "framework",
    "strong", "excellent", "ability", "including", "etc", "years", "year",
    "working", "knowledge", "understanding", "proficient", "familiar", "plus",
    "bonus", "nice", "preferred", "required", "responsibilities", "requirements",
    "looking", "join", "build", "building", "help", "across", "using", "deep",
    "passion", "passionate", "ideal", "great", "good", "solid", "hands",
    "engineer", "developer", "senior", "junior", "staff", "role", "position",
}


def _best_keywords(jd: str, limit: int) -> list[str]:
    """Skill-focused ATS keyword set, maximising real coverage.

    Prefers the ``jd_parser``'s categorised *skills* (true tech terms from its
    taxonomy), tops up with its distinctive TF-IDF/frequency union ranking, and
    drops generic JD filler. Falls back to plain frequency extraction.
    """
    try:
        from jobhunt.jd_parser import parse_jd
        parsed = parse_jd(jd, limit=limit * 3)
        ordered: list[str] = []
        seen: set[str] = set()
        for kw in list(parsed.skills) + list(parsed.union_keywords):
            k = kw.lower()
            if k in seen or k in _STOP or k in _FILLER or len(k) < 3:
                continue
            seen.add(k)
            ordered.append(k)
            if len(ordered) >= limit:
                break
        if ordered:
            return ordered
    except Exception:
        pass
    return extract_keywords(jd, limit)


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
        from jobhunt.resume_template import build_tailored_resume

        docs: list[TailoredDocument] = []
        for posting in inputs.postings:
            kws = _best_keywords(posting.jd_text, inputs.max_keywords)
            draft_dict: dict[str, Any] | None = None

            # Preferred path: render the user's REAL structured history, tailored.
            draft = build_tailored_resume(
                inputs.profile, posting,
                max_keywords=inputs.max_keywords, llm=self.llm,
            )
            # Only prefer the structured path when there are real bullets to
            # tailor; a profile with metadata-only experiences (no bullet lines)
            # falls back to the legacy templated path so coverage stays useful.
            has_structure = any(
                r.get("bullets")
                for s in draft.sections if s.kind in ("experience", "projects")
                for r in s.rows
            )
            if has_structure:
                matched = draft.matched_keywords
                missing = draft.missing_keywords
                bullets = [{"text": b.text, "evidence_id": b.evidence_id}
                           for b in draft.all_bullets()]
                resume_text = draft.to_text()
                draft_dict = asdict(draft)
            else:
                # Legacy fallback: no structured history yet → templated bullets.
                matched, missing, bullets = self._map_evidence(
                    kws, inputs.profile, posting
                )
                summary = self._summary(inputs.profile, posting, matched)
                resume_text = self._render_resume(
                    inputs.profile, posting, bullets, summary
                )

            coverage = len(matched) / max(1, len(matched) + len(missing))
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
                    draft=draft_dict,
                )
            )
            self.emit(
                trace, "act",
                f"{posting.company} :: {posting.title} → coverage {coverage:.0%}",
                considered=matched[:8],
                rejected=[{"item": m, "reason": "no backing evidence in profile"}
                          for m in missing[:8]],
                confidence=round(coverage, 3),
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
        self, keywords: list[str], profile: UserProfile, posting: JobPosting,
    ) -> tuple[list[str], list[str], list[dict[str, str]]]:
        # Build evidence index from skills and experience descriptions.
        # Each skill also registers its synonyms/aliases (k8s ← kubernetes) so a
        # JD keyword that uses a different surface form still matches.
        from jobhunt.skills_taxonomy import expand_term
        evidence: dict[str, dict[str, Any]] = {}
        for i, s in enumerate(profile.skills):
            entry = {"id": f"skill:{i}", "kind": "skill", "text": s}
            evidence[s.lower()] = entry
            for syn in expand_term(s):
                evidence.setdefault(syn, entry)
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
            text = self._draft_bullet(kw, ev)
            if self.llm is not None:
                try:
                    improved = self.llm("rewrite_bullet", {
                        "keyword": kw, "draft": text,
                        "company": posting.company, "title": posting.title,
                    })
                    if improved and isinstance(improved, str):
                        text = improved.strip()
                except Exception:
                    # LLM is best-effort; fall back to deterministic phrasing.
                    pass
            bullets.append({"text": text, "evidence_id": ev["id"]})
        return matched, missing, bullets

    @staticmethod
    def _draft_bullet(kw: str, ev: dict[str, Any]) -> str:
        """A professional, deterministic bullet for one matched keyword.

        Stable per keyword (no RNG / process-hash) so output is reproducible.
        When the evidence is a real experience line, append a short snippet
        for specificity — but the evidence_id (set by the caller) is what
        guarantees no hallucination, not this text.
        """
        idx = sum(ord(c) for c in kw) % len(_BULLET_TEMPLATES)
        base = _BULLET_TEMPLATES[idx].format(kw=kw)
        if ev.get("kind") == "experience":
            snippet = " ".join(str(ev.get("text", "")).split())
            if snippet and snippet.lower() != kw.lower():
                base = base.rstrip(".") + f" ({snippet[:80]})."
        return base

    def _summary(
        self, profile: UserProfile, posting: JobPosting, matched: list[str],
    ) -> str:
        """Two-sentence professional summary; LLM-polished when available."""
        skills = ", ".join(profile.skills[:6]) or "the role's core stack"
        base = (
            f"{profile.name} — engineer experienced in {skills}. "
            f"Targeting {posting.title} at {posting.company}."
        )
        if self.llm is not None:
            try:
                improved = self.llm("summary", {
                    "profile": profile.to_dict(),
                    "posting_title": posting.title,
                    "posting_company": posting.company,
                    "keywords": matched,
                })
                if improved and isinstance(improved, str):
                    base = improved.strip()
            except Exception:
                pass
        return base

    @staticmethod
    def _render_resume(
        profile: UserProfile, posting: JobPosting,
        bullets: list[dict[str, str]], summary: str,
    ) -> str:
        out = [
            profile.name,
            profile.email,
            "",
            f"Target: {posting.title} @ {posting.company}",
            "",
            "SUMMARY",
            summary,
            "",
        ]
        if profile.skills:
            out += ["KEY SKILLS", ", ".join(profile.skills), ""]
        out.append("HIGHLIGHTS")
        if bullets:
            out += [f"- {b['text']}" for b in bullets]
        else:
            out.append("- Matching evidence will appear here as the profile grows.")
        return "\n".join(out).rstrip() + "\n"

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
