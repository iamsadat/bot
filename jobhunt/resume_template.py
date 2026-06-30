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

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

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
    # Layout-aware structured rows for the single-column template renderer.
    # ``kind`` is one of summary|experience|projects|education|skills|generic.
    # Each row: {"left": str (supports **bold**), "right": str (right-aligned),
    #            "link": str, "bullets": [{"text": str, "evidence_id": str}]}.
    kind: str = "generic"
    rows: list[dict] = field(default_factory=list)


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
    phone: str = ""
    location: str = ""
    links: dict[str, str] = field(default_factory=dict)

    def contact_line(self) -> str:
        parts = [p for p in (self.candidate_email, self.phone, self.location) if p]
        parts += [v for v in self.links.values() if v]
        return "  |  ".join(parts)

    def to_text(self) -> str:
        """Plain text renderer (no external deps).

        Renders structured ``rows`` when present (the tailored path), else the
        legacy bullets/body. Keeps ALL-CAPS headings + ``- `` bullets so the
        text→pdf/docx fallback in resume_renderer still parses it.
        """
        out = [self.candidate_name]
        contact = self.contact_line()
        if contact:
            out.append(contact)
        out.append("")
        if self.summary:
            out += [self.summary, ""]
        for sec in self.sections:
            if sec.kind == "summary":
                continue
            out.append(sec.title.upper())
            if sec.body:
                out.append(sec.body)
            if sec.rows:
                for row in sec.rows:
                    left = _strip_bold(str(row.get("left", "")))
                    right = str(row.get("right", "") or row.get("link", ""))
                    header = f"{left}  —  {right}" if right else left
                    if header.strip():
                        out.append(header)
                    for b in row.get("bullets", []):
                        out.append(f"- {b['text'] if isinstance(b, dict) else b}")
            for b in sec.bullets:
                out.append(f"- {b.text}")
            out.append("")
        return "\n".join(out).strip() + "\n"

    def all_bullets(self) -> list[Bullet]:
        flat = [b for s in self.sections for b in s.bullets]
        for s in self.sections:
            for row in s.rows:
                for b in row.get("bullets", []):
                    if isinstance(b, dict):
                        flat.append(Bullet(text=b.get("text", ""),
                                           evidence_id=b.get("evidence_id", "")))
        return flat

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeDraft":
        """Reconstruct a draft from ``dataclasses.asdict`` output (round-trip)."""
        sections = []
        for s in d.get("sections", []):
            sections.append(ResumeSection(
                title=s.get("title", ""),
                bullets=[Bullet(**b) if isinstance(b, dict) else b
                         for b in s.get("bullets", [])],
                body=s.get("body", ""),
                kind=s.get("kind", "generic"),
                rows=list(s.get("rows", [])),
            ))
        return cls(
            candidate_name=d.get("candidate_name", ""),
            candidate_email=d.get("candidate_email", ""),
            target_role=d.get("target_role", ""),
            target_company=d.get("target_company", ""),
            summary=d.get("summary", ""),
            sections=sections,
            matched_keywords=list(d.get("matched_keywords", [])),
            missing_keywords=list(d.get("missing_keywords", [])),
            phone=d.get("phone", ""),
            location=d.get("location", ""),
            links=dict(d.get("links", {})),
        )


def _strip_bold(s: str) -> str:
    return s.replace("**", "")


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
            logger.debug("LLM bullet rewrite failed for keyword=%r", keyword, exc_info=True)
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
            logger.debug("LLM summary generation failed", exc_info=True)

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


# --------------------------------------------------------------------------- #
# Tailored, layout-aware resume builder (the quality path)
#
# Produces a ResumeDraft with structured ``rows`` for the single-column
# template renderer, populated from the user's REAL structured history. Bullets
# are reordered/emphasised by JD relevance but never invented — every bullet
# keeps an evidence_id pointing at the source experience/project line.
# --------------------------------------------------------------------------- #

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{1,}")


def _text_tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def _expanded_keywords(keywords: list[str]) -> set[str]:
    from jobhunt.skills_taxonomy import expand_term
    out: set[str] = set()
    for kw in keywords:
        out.add(kw.lower())
        try:
            out.update(s.lower() for s in expand_term(kw))
        except Exception:
            logger.debug("expand_term failed for %r", kw, exc_info=True)
    return out


def _embed_sim(text: str, jd: str) -> float:
    try:
        from jobhunt.embeddings import cosine_similarity, embed_jd_text
        return float(cosine_similarity(embed_jd_text(text), embed_jd_text(jd)))
    except Exception:
        logger.debug("embedding similarity computation failed", exc_info=True)
        return 0.0


def _bullet_relevance(text: str, expanded: set[str], jd: str) -> float:
    overlap = len(_text_tokens(text) & expanded)
    return overlap + 0.5 * _embed_sim(text, jd)


def _date_range(start: str, end: str) -> str:
    start, end = (start or "").strip(), (end or "").strip()
    if start and end:
        return f"{start} – {end}"
    return end or start


def _recency_key(start: str, end: str) -> int:
    """Sortable recency from a date range; ongoing roles rank highest."""
    s = f"{end or ''} {start or ''}"
    if re.search(r"present|current|now|ongoing", s, re.I):
        return 999999
    years = re.findall(r"(?:19|20)\d{2}", s)
    return int(years[-1]) if years else 0


def _polish(text: str, posting: JobPosting, llm: Callable[[str, dict], str] | None,
            ) -> tuple[str, bool]:
    """Best-effort LLM tone-polish; falls back to the original verbatim text."""
    if llm is None or not text.strip():
        return text, False
    try:
        improved = llm("rewrite_bullet", {
            "keyword": "", "draft": text,
            "company": posting.company, "title": posting.title,
        })
        if improved and isinstance(improved, str):
            return improved.strip(), True
    except Exception:
        logger.debug("LLM tone-polish failed", exc_info=True)
    return text, False


def build_tailored_resume(
    profile: UserProfile,
    posting: JobPosting,
    *,
    max_keywords: int = 15,
    llm: Callable[[str, dict], str] | None = None,
) -> ResumeDraft:
    """Build a layout-ready, JD-tailored ResumeDraft from a structured profile.

    Reorders each experience/project's real bullets by relevance to the JD and
    orders entries by their strongest bullet. Never fabricates: every bullet
    carries an ``evidence_id`` referencing the source line.
    """
    from jobhunt.agents.resume import _best_keywords
    from jobhunt.skills_taxonomy import expand_term

    jd = posting.jd_text or ""
    keywords = _best_keywords(jd, max_keywords)
    expanded = _expanded_keywords(keywords)

    sections: list[ResumeSection] = []
    corpus_tokens: set[str] = {s.lower() for s in profile.skills}

    # ----- Experience ---------------------------------------------------------
    # Entries are reverse-chronological (resume convention); BULLETS within each
    # entry are reordered by JD relevance (the tailoring), tie-broken by recency.
    exp_entries: list[tuple[int, float, dict]] = []
    for i, e in enumerate(profile.structured_experiences()):
        scored: list[tuple[float, dict]] = []
        for k, btext in enumerate(e.bullets):
            if not str(btext).strip():
                continue
            score = _bullet_relevance(str(btext), expanded, jd)
            text, _ = _polish(str(btext), posting, llm)
            corpus_tokens |= _text_tokens(str(btext))
            scored.append((score, {"text": text, "evidence_id": f"exp:{i}:bullet:{k}"}))
        scored.sort(key=lambda x: -x[0])
        bullets = [b for _, b in scored]
        entry_score = scored[0][0] if scored else 0.0
        label = ", ".join(p for p in (e.title, e.company) if p)
        left = f"**{label}**" if label else ""
        if e.location:
            left = f"{left}, {e.location}" if left else e.location
        exp_entries.append((_recency_key(e.start, e.end), entry_score, {
            "left": left, "right": _date_range(e.start, e.end),
            "link": "", "bullets": bullets,
        }))
    exp_entries.sort(key=lambda x: (-x[0], -x[1]))
    exp_rows = [r for _, _, r in exp_entries if r["left"] or r["bullets"]]
    if exp_rows:
        sections.append(ResumeSection(title="Experience", kind="experience", rows=exp_rows))

    # ----- Projects -----------------------------------------------------------
    proj_rows: list[dict] = []
    for i, p in enumerate(profile.structured_projects()):
        scored = []
        for k, btext in enumerate(p.bullets):
            if not str(btext).strip():
                continue
            score = _bullet_relevance(str(btext), expanded, jd)
            text, _ = _polish(str(btext), posting, llm)
            corpus_tokens |= _text_tokens(str(btext))
            scored.append((score, {"text": text, "evidence_id": f"proj:{i}:bullet:{k}"}))
        scored.sort(key=lambda x: -x[0])
        bullets = [b for _, b in scored]
        if not bullets and p.description:
            corpus_tokens |= _text_tokens(p.description)
            bullets = [{"text": p.description, "evidence_id": f"proj:{i}:desc"}]
        cat = ", ".join(p.skills[:2]) if p.skills else ""
        left = f"**{p.name}**" + (f", {cat}" if cat else "")
        proj_rows.append({"left": left, "right": p.link, "link": p.link, "bullets": bullets})
    proj_rows = [r for r in proj_rows if r["left"].strip("* ")]
    if proj_rows:
        sections.append(ResumeSection(title="Projects", kind="projects", rows=proj_rows))

    # ----- Skills -------------------------------------------------------------
    if profile.skills:
        sections.append(ResumeSection(
            title="Skills", kind="skills", body=", ".join(profile.skills),
        ))

    # ----- Education ----------------------------------------------------------
    edu_rows: list[dict] = []
    for e in profile.structured_education():
        deg = ", ".join(x for x in (e.degree, e.field) if x)
        head = f"{deg}, {e.school}" if deg and e.school else (deg or e.school)
        if not head:
            continue
        edu_rows.append({"left": f"**{head}**", "right": e.end or e.start,
                         "link": "", "bullets": []})
    if edu_rows:
        sections.append(ResumeSection(title="Education", kind="education", rows=edu_rows))

    # ----- Summary (tailored, LLM-polished when available) --------------------
    skills_str = ", ".join(profile.skills[:6]) or "the role's core stack"
    summary = (f"{profile.name} — engineer experienced in {skills_str}. "
               f"Targeting {posting.title} at {posting.company}.")
    if llm is not None:
        try:
            improved = llm("summary", {
                "profile": profile.to_dict(),
                "posting_title": posting.title,
                "posting_company": posting.company,
                "keywords": keywords,
            })
            if improved and isinstance(improved, str):
                summary = improved.strip()
        except Exception:
            logger.debug("LLM summary polish failed in build_tailored_resume", exc_info=True)

    # ----- matched / missing coverage ----------------------------------------
    matched: list[str] = []
    missing: list[str] = []
    for kw in keywords:
        forms = {kw.lower()}
        try:
            forms |= {s.lower() for s in expand_term(kw)}
        except Exception:
            logger.debug("expand_term failed for %r in build_tailored_resume", kw, exc_info=True)
        (matched if forms & corpus_tokens else missing).append(kw)

    return ResumeDraft(
        candidate_name=profile.name,
        candidate_email=profile.email,
        target_role=posting.title,
        target_company=posting.company,
        summary=summary,
        sections=sections,
        matched_keywords=matched,
        missing_keywords=missing,
        phone=profile.phone,
        location=(profile.locations[0] if profile.locations else ""),
        links=dict(profile.links),
    )
