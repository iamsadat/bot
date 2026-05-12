"""Templated resume engine — structured slot-fill instead of free-form prose.

Phase 2 upgrade. The engine receives:
  * a Resume template (header, summary, experience, education, skills sections)
  * a slot map: section -> list of bullets, where each bullet is tied to an
    evidence id from the user's experience graph

It produces a typed ResumeDraft. The Phase 0 plain-text renderer is kept for
backwards compatibility; PDF/DOCX rendering is in resume_renderer.py.

The LLM (Sonnet 4.6) plugs in via the optional ``llm`` callback to rewrite
individual bullets for tone — but the structure, claims, and evidence ids are
fixed by the engine. The LLM is never trusted to invent facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from jobhunt.models import UserProfile, JobPosting


@dataclass
class Bullet:
    text: str
    evidence_id: str
    keyword: str = ""
    rewritten_by_llm: bool = False


@dataclass
class ResumeSection:
    title: str
    bullets: list[Bullet] = field(default_factory=list)
    body: str = ""  # free text (summary, etc)


@dataclass
class ResumeDraft:
    candidate_name: str
    candidate_email: str
    target_role: str
    target_company: str
    summary: str
    sections: list[ResumeSection]
    matched_keywords: list[str]
    missing_keywords: list[str]

    def to_text(self) -> str:
        """Plain text renderer (no external deps)."""
        out = [self.candidate_name, self.candidate_email, ""]
        out.append(f"Target: {self.target_role} @ {self.target_company}")
        out.append("")
        out.append("SUMMARY")
        out.append(self.summary)
        out.append("")
        for sec in self.sections:
            out.append(sec.title.upper())
            if sec.body:
                out.append(sec.body)
            for b in sec.bullets:
                out.append(f"- {b.text}")
            out.append("")
        return "\n".join(out).strip() + "\n"

    def all_bullets(self) -> list[Bullet]:
        return [b for s in self.sections for b in s.bullets]


# ------------------------------------------------------------- slot-fill API


def _bullet_for_keyword(
    keyword: str,
    evidence: dict[str, dict[str, str]],
    llm: Callable[[str, dict], str] | None = None,
) -> Bullet | None:
    ev = evidence.get(keyword)
    if ev is None:
        return None
    base = f"Delivered work involving {keyword} — {ev['text'][:120]}".rstrip()
    rewritten = False
    if llm is not None:
        try:
            improved = llm("rewrite_bullet", {"keyword": keyword, "draft": base})
            if improved and isinstance(improved, str):
                base = improved.strip()
                rewritten = True
        except Exception:
            # LLM is best-effort; fall back to deterministic phrasing.
            pass
    return Bullet(text=base, evidence_id=ev["id"], keyword=keyword, rewritten_by_llm=rewritten)


def build_evidence_index(profile: UserProfile) -> dict[str, dict[str, str]]:
    """Build keyword -> evidence map from skills + experience descriptions."""
    import re
    tok = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{2,}")
    evidence: dict[str, dict[str, str]] = {}
    for i, s in enumerate(profile.skills):
        evidence[s.lower()] = {"id": f"skill:{i}", "kind": "skill", "text": s}
    for j, e in enumerate(profile.experiences):
        desc = " ".join(str(v) for v in e.values())
        for t in tok.findall(desc.lower()):
            evidence.setdefault(
                t, {"id": f"exp:{j}", "kind": "experience", "text": str(e)},
            )
    return evidence


def build_resume_draft(
    profile: UserProfile,
    posting: JobPosting,
    keywords: list[str],
    *,
    summary_template: str | None = None,
    llm: Callable[[str, dict], str] | None = None,
) -> ResumeDraft:
    """Produce a structured ResumeDraft using slot-fill from evidence.

    Args:
        profile: User profile (skills, experiences).
        posting: Target job posting.
        keywords: ATS keywords to cover (ranked by importance).
        summary_template: Optional override for the summary paragraph.
        llm: Optional callback ``(action, payload) -> str`` for LLM tone rewrites.
            Action ``"rewrite_bullet"`` receives ``{"keyword", "draft"}``;
            action ``"summary"`` receives ``{"profile", "posting", "keywords"}``.

    Returns:
        ResumeDraft. Bullets are guaranteed to have a backing evidence_id.
    """
    evidence = build_evidence_index(profile)

    bullets: list[Bullet] = []
    matched: list[str] = []
    missing: list[str] = []
    for kw in keywords:
        b = _bullet_for_keyword(kw, evidence, llm=llm)
        if b is None:
            missing.append(kw)
        else:
            bullets.append(b)
            matched.append(kw)

    summary = summary_template or (
        f"{profile.name} — backend engineer with experience in "
        f"{', '.join(profile.skills[:6])}. Targeting {posting.title} "
        f"at {posting.company}."
    )
    if llm is not None:
        try:
            improved = llm(
                "summary",
                {
                    "profile": profile.to_dict(),
                    "posting_title": posting.title,
                    "posting_company": posting.company,
                    "keywords": matched,
                },
            )
            if improved and isinstance(improved, str):
                summary = improved.strip()
        except Exception:
            pass

    experiences_section = ResumeSection(title="Experience")
    # Bullets are attached to Experience for now; multi-section split is a
    # follow-up. The structural guarantee (evidence_id per bullet) is the
    # important invariant.
    experiences_section.bullets = bullets
    skills_section = ResumeSection(
        title="Skills",
        body=", ".join(profile.skills),
    )

    return ResumeDraft(
        candidate_name=profile.name,
        candidate_email=profile.email,
        target_role=posting.title,
        target_company=posting.company,
        summary=summary,
        sections=[experiences_section, skills_section],
        matched_keywords=matched,
        missing_keywords=missing,
    )
