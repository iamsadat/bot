"""Inter-agent peer critique — the Vetting Agent reviews tailored résumés.

After the Resume Architect produces a TailoredDocument, the Vetting Agent
runs a structured critique against the company context (culture, tech
stack, role seniority). This is *not* duplicate work — Resume Architect's
self-critique is about hallucination and ATS coverage; the peer critique
asks "does this résumé suit *this* company specifically?".

Outputs a PeerCritique with explicit signals and a hold-or-ship verdict.
The dashboard renders the flags so the human reviewer can act on them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from jobhunt.agents.base import BaseAgent
from jobhunt.models import JobPosting, ReasoningTrace
from jobhunt.resume_template import ResumeDraft


# Culture-signal lexicon. Maps a culture vector to the words a JD/résumé
# uses when it falls into that bucket. Wrong-bucket résumés trigger flags.
_CULTURE_LEXICON: dict[str, set[str]] = {
    "startup": {
        "startup", "scrappy", "ownership", "ship", "fast", "0-to-1",
        "iterate", "founding", "early", "wear-many-hats",
    },
    "enterprise": {
        "enterprise", "compliance", "soc2", "governance", "policy",
        "stakeholder", "alignment", "process", "framework",
    },
    "research": {
        "research", "paper", "publication", "novel", "hypothesis",
        "experiment", "phd", "academic",
    },
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{2,}")


@dataclass
class PeerCritique:
    document_id: str
    company: str
    score: float                              # 0..1 overall fit
    culture_alignment: float                  # 0..1
    keyword_density: float                    # 0..1 (matched/required keywords)
    evidence_diversity: float                 # 0..1 (distinct evidence ids / bullets)
    flags: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    verdict: str = "hold"                     # "ship" | "hold" | "rework"


@dataclass
class PeerCritiqueInputs:
    draft: ResumeDraft
    posting: JobPosting
    document_id: str = ""
    required_keywords: list[str] = field(default_factory=list)
    pass_threshold: float = 0.65


def detect_culture(text: str) -> dict[str, float]:
    """Return per-bucket weights (sum may be < 1 — uncategorised content allowed)."""
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return {k: 0.0 for k in _CULTURE_LEXICON}
    counts = Counter(tokens)
    n = sum(counts.values())
    return {
        bucket: round(sum(counts.get(w, 0) for w in words) / n, 4)
        for bucket, words in _CULTURE_LEXICON.items()
    }


def _alignment(culture_jd: dict[str, float], culture_resume: dict[str, float]) -> float:
    """Cosine-style alignment over the culture vector."""
    keys = list(_CULTURE_LEXICON)
    a = [culture_jd.get(k, 0.0) for k in keys]
    b = [culture_resume.get(k, 0.0) for k in keys]
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        # No culture signal on either side — neutral, neither penalise nor reward.
        return 0.6
    return min(1.0, dot / (na * nb))


class PeerCritiqueAgent(BaseAgent[PeerCritiqueInputs, PeerCritique]):
    """The Vetting Agent acting as a peer reviewer for tailored résumés."""

    name = "peer_critique"
    quality_threshold = 0.6
    max_refinements = 0  # critique is single-shot

    def deliberate(
        self, inputs: PeerCritiqueInputs, trace: ReasoningTrace,
    ) -> list[str]:
        return [
            f"peer-reviewing résumé for {inputs.posting.company} "
            f"({inputs.posting.title}).",
            "checks: culture alignment vs JD, ATS keyword density, "
            "evidence diversity, suspicious claims.",
            f"verdict thresholds: ship ≥ {inputs.pass_threshold}, "
            "hold otherwise; rework if any flag set.",
        ]

    def act(
        self, inputs: PeerCritiqueInputs, trace: ReasoningTrace,
    ) -> PeerCritique:
        draft = inputs.draft
        posting = inputs.posting
        resume_text = draft.to_text()

        culture_jd = detect_culture(posting.jd_text)
        culture_resume = detect_culture(resume_text)
        culture_align = round(_alignment(culture_jd, culture_resume), 3)

        required = [k.lower() for k in inputs.required_keywords] \
                   or [k.lower() for k in draft.matched_keywords + draft.missing_keywords]
        matched = [k for k in required if k in draft.matched_keywords]
        density = round(len(matched) / max(1, len(required)), 3)

        bullets = draft.all_bullets()
        if bullets:
            distinct = len({b.evidence_id for b in bullets})
            diversity = round(distinct / len(bullets), 3)
        else:
            diversity = 0.0

        flags: list[str] = []
        suggestions: list[str] = []
        if culture_align < 0.5:
            top_jd = max(culture_jd, key=culture_jd.get) if culture_jd else "?"
            flags.append(
                f"culture_mismatch: JD leans '{top_jd}', résumé doesn't echo it"
            )
            suggestions.append(
                f"Add 1 bullet using '{top_jd}'-aligned phrasing if you have evidence."
            )
        if density < 0.5:
            missing = [k for k in required if k not in draft.matched_keywords]
            flags.append(f"low_keyword_density: {density:.2f}")
            suggestions.append(
                f"Consider adding evidence for: {', '.join(missing[:3])}"
                + ("…" if len(missing) > 3 else "")
            )
        if diversity < 0.5 and len(bullets) >= 3:
            flags.append("evidence_concentration: bullets lean on one experience")
            suggestions.append(
                "Spread bullets across distinct experiences/skills."
            )

        score = round(
            0.45 * culture_align + 0.35 * density + 0.20 * diversity, 3,
        )
        if score >= inputs.pass_threshold and not flags:
            verdict = "ship"
        elif score < 0.4:
            verdict = "rework"
        else:
            verdict = "hold"

        self.think(
            trace,
            f"{posting.company}: score={score} "
            f"(culture={culture_align}, density={density}, diversity={diversity}) "
            f"verdict={verdict} flags={flags}",
        )

        return PeerCritique(
            document_id=inputs.document_id,
            company=posting.company,
            score=score,
            culture_alignment=culture_align,
            keyword_density=density,
            evidence_diversity=diversity,
            flags=flags,
            suggestions=suggestions,
            verdict=verdict,
        )

    def critique(
        self,
        inputs: PeerCritiqueInputs,
        output: PeerCritique,
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        # Meta-critique: did we surface enough signal?
        explainability = 1.0 if output.flags or output.score > 0.7 else 0.7
        return {
            "explainability": explainability,
            "verdict_consistency": 1.0
                if (output.verdict == "ship") == (output.score >= inputs.pass_threshold and not output.flags)
                else 0.5,
        }

    def decide(
        self,
        inputs: PeerCritiqueInputs,
        output: PeerCritique,
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        return (
            f"peer-review verdict={output.verdict} for {output.company} "
            f"(score={output.score})",
            avg,
        )
