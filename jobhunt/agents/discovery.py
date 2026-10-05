"""Job Discovery & Intelligence Agent.

Plans queries → fans out to source adapters → normalizes → dedupes →
relevance + ghost-job scoring → emits a ranked DiscoveryBatch.

Relevance is an explainable composite of four signals — title match, skill
coverage, seniority fit and location fit — each reported alongside the total so
the UI can say *why* a job scored what it did. See :func:`score`.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from jobhunt.adapters.base import JobSource
from jobhunt.adapters.filters import ANYWHERE as _ANYWHERE_PLACES
from jobhunt.adapters.filters import passes_local_filters, place_matches, remote_scope, words
from jobhunt.agents.base import BaseAgent
from jobhunt.models import (
    DiscoveryBatch,
    JobPosting,
    ReasoningTrace,
    UserProfile,
)
from jobhunt.seniority import LEVEL_NAMES
from jobhunt.seniority import fit as seniority_fit
from jobhunt.seniority import level_from_posting, level_from_profile, within_band
from jobhunt.skills_taxonomy import canonical, expand_terms, skills_in_text


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]*")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# Weights for the four components of a match. Title dominates because it is the
# one field that says what the job *is*; everything else refines a role that is
# already plausible.
_W_TITLE, _W_SKILLS, _W_SENIORITY, _W_LOCATION = 0.40, 0.35, 0.15, 0.10

# Title words that describe no particular job. "Engineer" is shared by every
# engineering role on earth, so matching it alone must not read as a fit.
_GENERIC_TITLE_WORDS = frozenset({
    "engineer", "engineering", "developer", "development", "analyst", "manager",
    "specialist", "consultant", "scientist", "architect", "administrator",
    "associate", "professional", "expert", "officer", "coordinator", "technician",
    "senior", "junior", "staff", "principal", "lead", "head", "chief", "director",
    "intern", "graduate", "entry", "mid", "sr", "jr", "i", "ii", "iii", "iv",
    "and", "or", "of", "the", "for", "with", "in", "at", "m", "w", "d", "f",
})

# Level markers are scored separately by seniority_fit; leaving them in the title
# comparison would double-count, and would make "Junior Data Engineer" a poor
# title match for the target role "Data Engineer".
_LEVEL_WORDS = frozenset({
    "senior", "junior", "staff", "principal", "lead", "head", "chief", "intern",
    "graduate", "entry", "mid", "midweight", "sr", "jr", "trainee", "director",
})

# Below this, a JD is a listing snippet rather than a description. Adzuna returns
# ~200 characters; scoring absent text as "skills not found" would punish the
# source with the best regional coverage.
_MIN_JD_FOR_SKILLS = 400

# A JD naming one or two technologies gives no reliable coverage ratio — one
# incidental match reads as 50%. A tax-advisor listing that happens to mention
# Excel scored 34% overall this way.
_MIN_SKILLS_FOR_RATIO = 4

# Stands in for skill coverage when the JD is too thin to measure it. Halfway is
# the honest answer to "we cannot tell".
_UNKNOWN_SKILL_FIT = 0.5

# What a job can score when its title matches nothing the candidate is looking
# for. Skills and location can only ever make a wrong role look like a near
# miss, never a match, so the total is capped rather than averaged.
_WRONG_ROLE_CEILING = 0.25
# A role outside the candidate's level band (see seniority.within_band) is not a
# match however well the title and skills line up: seniority is only 15% of the
# weight, so without a cap a Staff role scored 86% for a junior.
_WRONG_LEVEL_CEILING = 0.35


def _title_forms(text: str) -> set[str]:
    """Tokens of a title plus their taxonomy synonyms, lowercased."""
    tokens = set(_tokenize(text))
    return {t.lower() for t in expand_terms(tokens)} | tokens


def title_fit(posting_title: str, target_roles: list[str]) -> float:
    """0..1 — how well a posting's title matches any target role.

    Containment of the role's words in the title, not cosine: the question is
    "does this title describe the job I want", and a long title should not be
    penalised for carrying extra words.

    A role's *distinctive* words carry the match. Matching only generic words
    caps the score low, which is what stops "Senior Backend Engineer" from
    reading as a hit for "data engineer" purely on the word "engineer".
    """
    if not posting_title or not target_roles:
        return 0.0
    title = _title_forms(posting_title)
    best = 0.0
    for role in target_roles:
        role_tokens = {t for t in _tokenize(role or "")} - _LEVEL_WORDS
        if not role_tokens:
            continue
        matched = {t for t in role_tokens if t in title}
        if not matched:
            continue
        distinctive = role_tokens - _GENERIC_TITLE_WORDS
        if distinctive and not (distinctive & title):
            # Generic-only overlap: related field at best, not this job.
            best = max(best, 0.2 * len(matched) / len(role_tokens))
            continue
        best = max(best, len(matched) / len(role_tokens))
    return round(min(1.0, best), 4)


def skill_fit(jd_text: str, skills: list[str]) -> tuple[float, list[str], list[str]]:
    """Fraction of the technologies a JD names that the candidate has.

    The denominator is the JD's asks, not the candidate's skill list — "I cover
    70% of what they want" is actionable, whereas dividing by the candidate's
    skills just penalises anyone who knows a lot of things.

    Both sides are reduced to canonical taxonomy terms first, so a JD asking for
    "k8s" is satisfied by a résumé saying "Kubernetes" and neither is counted
    twice.
    """
    required = skills_in_text(jd_text)
    if not required:
        return 0.0, [], []
    have = {canonical(s) for s in skills if s}

    matched = sorted(required & have)
    missing = sorted(required - have)
    return round(len(matched) / len(required), 4), matched, missing


def location_fit(posting: JobPosting, profile: UserProfile) -> float:
    """0..1 — how well a posting's location suits the candidate.

    Uses whole-word place matching, and reads a remote role's declared scope
    rather than treating "remote" as a universal yes. Substring comparison had
    scored "Remote US" as a perfect location for a candidate in Hyderabad,
    simply because the word "remote" appeared in both.
    """
    wanted = [loc.strip() for loc in (profile.locations or []) if loc.strip()]
    real = [loc for loc in wanted if loc.lower() not in _ANYWHERE_PLACES]
    if not wanted:
        return 1.0

    # Sited where the candidate actually is.
    if any(place_matches(loc, posting.location) for loc in real):
        return 1.0

    if posting.remote and (profile.remote_ok or wanted != real):
        scope = remote_scope(posting)
        if not scope or any(words(s) & _ANYWHERE_PLACES for s in scope):
            # Open remote: a real answer, but weaker than a role in their city.
            return 0.9 if wanted != real else 0.8
        if not real:
            return 0.8
        if any(place_matches(w, s) or place_matches(s, w) for s in scope for w in real):
            return 1.0
        return 0.15  # remote, but not open to where they are
    return 0.2


def score(posting: JobPosting, profile: UserProfile) -> dict[str, Any]:
    """Explainable 0..1 match score with its per-component breakdown.

    Replaces a term-frequency cosine between the profile and the whole JD. That
    formula was structurally incapable of scoring well: a ~40-token profile
    vector against a ~900-token JD vector caps out near 0.15 no matter how
    perfect the fit, so the best job on a board read as "12% match". Measured
    over a live board, the old function's maximum was 0.136 and its median
    0.011 — it was reporting document length, not suitability.
    """
    t = title_fit(posting.title, list(profile.target_roles))
    sk, matched, missing = skill_fit(posting.jd_text, list(profile.skills))
    cand_level = level_from_profile(profile)
    post_level = level_from_posting(posting)
    sen = seniority_fit(cand_level, post_level)
    loc = location_fit(posting, profile)

    # A snippet-length JD, or one naming barely any technology, is thin evidence
    # — but only in one direction. Absent text must not read as "the candidate
    # lacks these skills", while skills the snippet *does* name are real evidence
    # worth crediting. So a thin JD's skill score is floored at "unknown": it can
    # lift a match, never sink one.
    #
    # The trade-off is that a thin listing can out-rank a fully documented weak
    # match. That is the intended direction — we genuinely do not know the thin
    # one is weak — and it keeps Adzuna, the source with the best regional
    # coverage, from being buried for returning short descriptions.
    required = len(matched) + len(missing)
    skills_scored = len(posting.jd_text or "") >= _MIN_JD_FOR_SKILLS and (
        required >= _MIN_SKILLS_FOR_RATIO
        # Matching *none* of a JD's named technologies is real evidence even from
        # a short list. Waiving the component here scored an Android role at 82%
        # for a candidate with no Android or Kotlin, because its description
        # happened to name only those two.
        or (required >= 2 and not matched)
    )
    effective_sk = sk if skills_scored else max(sk, _UNKNOWN_SKILL_FIT)

    parts = [
        (_W_TITLE, t), (_W_SENIORITY, sen), (_W_LOCATION, loc),
        (_W_SKILLS, effective_sk),
    ]
    total = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    if t <= 0.0:
        total = min(total, _WRONG_ROLE_CEILING)
    if not within_band(cand_level, post_level):
        total = min(total, _WRONG_LEVEL_CEILING)

    return {
        "total": round(min(1.0, max(0.0, total)), 4),
        "title": t,
        "skills": sk,
        "seniority": sen,
        "location": loc,
        "candidate_level": cand_level,
        "posting_level": post_level,
        "candidate_level_name": LEVEL_NAMES.get(cand_level) if cand_level is not None else None,
        "posting_level_name": LEVEL_NAMES.get(post_level) if post_level is not None else None,
        "matched_keywords": matched[:12],
        "missing_keywords": missing[:12],
        "skills_scored": skills_scored,
    }


def relevance(posting: JobPosting, profile: UserProfile) -> float:
    """0..1 relevance of a posting to the user. See :func:`score`."""
    return score(posting, profile)["total"]


def _weakest(breakdown: dict[str, Any]) -> str:
    """Name the component that sank a score, for the rejection reason."""
    parts = {k: breakdown.get(k, 1.0) for k in ("title", "skills", "seniority", "location")}
    if not breakdown.get("skills_scored", True):
        parts.pop("skills", None)
    worst = min(parts, key=lambda k: parts[k])
    if worst == "seniority" and breakdown.get("posting_level_name"):
        return f"{breakdown['posting_level_name']} role"
    return f"weak {worst} match"


def ghost_score(posting: JobPosting, now: float | None = None) -> float:
    """Heuristic: higher means more likely to be a ghost / stale post."""
    now = now or time.time()
    score = 0.0
    age_days = ((now - (posting.posted_at or now)) / 86400) if posting.posted_at else 0
    if age_days > 60:
        score += 0.5
    elif age_days > 30:
        score += 0.25
    if len(posting.jd_text) < 200:
        score += 0.2
    if "reposted" in posting.jd_text.lower():
        score += 0.3
    return round(min(score, 1.0), 4)


def dedupe(postings: list[JobPosting]) -> list[JobPosting]:
    """Keep the first posting per fingerprint; preserve order."""
    seen: set[str] = set()
    out: list[JobPosting] = []
    for p in postings:
        if p.fingerprint in seen:
            continue
        seen.add(p.fingerprint)
        out.append(p)
    return out


# ----------------------------------------------------------------- agent

@dataclass
class DiscoveryInputs:
    profile: UserProfile
    queries: list[dict]
    sources: list[JobSource]
    plan_id: str
    weekly_target: int = 10
    # A real fit floor, now that the score means something. The old default was
    # 0.05 because no posting could ever score higher than about 0.15.
    min_relevance: float = 0.4
    max_ghost: float = 0.5


class DiscoveryAgent(BaseAgent[DiscoveryInputs, DiscoveryBatch]):
    name = "discovery"
    quality_threshold = 0.6
    max_refinements = 0  # Discovery is non-deterministic across sources; do not loop.

    def deliberate(self, inputs: DiscoveryInputs, trace: ReasoningTrace) -> list[str]:
        return [
            f"received {len(inputs.queries)} queries across "
            f"{len(inputs.sources)} sources.",
            "plan: fan out per (query, source); apply dedupe by "
            "(company,title,location) fingerprint; score relevance against "
            "the user skill vector; flag ghost jobs by posting age and "
            "JD signals.",
            f"thresholds: relevance ≥ {inputs.min_relevance}, ghost ≤ "
            f"{inputs.max_ghost}; weekly_target={inputs.weekly_target}.",
        ]

    def act(self, inputs: DiscoveryInputs, trace: ReasoningTrace) -> DiscoveryBatch:
        all_postings: list[JobPosting] = []
        sources_used: list[str] = []
        degraded: list[str] = []

        for src in inputs.sources:
            sources_used.append(src.name)
            for q in inputs.queries:
                def _do_search(source=src, query=q):
                    # Board adapters filter inside search(); native-search
                    # sources (Adzuna) return whatever their keyword match
                    # found — Staff roles and sales jobs for "data engineer".
                    # Filtering here covers every source; it is idempotent.
                    return [p for p in source.search(query)
                            if passes_local_filters(p, query)]

                def _empty() -> list[JobPosting]:
                    return []

                results, was_degraded = self.call_tool(
                    trace,
                    f"source:{src.name}",
                    _do_search,
                    fallback=_empty,
                    args_summary=f"role={q.get('role')}, loc={q.get('location')}",
                )
                if was_degraded:
                    if src.name not in degraded:
                        degraded.append(src.name)
                    self.think(
                        trace,
                        f"source '{src.name}' degraded; continuing with "
                        "other sources.",
                    )
                    continue
                if results:
                    all_postings.extend(results)

        self.think(trace, f"raw postings fetched: {len(all_postings)}")
        before = len(all_postings)
        all_postings = dedupe(all_postings)
        self.think(trace, f"after dedupe: {len(all_postings)} (-{before - len(all_postings)})")

        ranked: list[JobPosting] = []
        rejected: list[dict[str, str]] = []
        for p in all_postings:
            p.score_breakdown = score(p, inputs.profile)
            p.relevance_score = p.score_breakdown["total"]
            p.ghost_score = ghost_score(p)
            if p.relevance_score < inputs.min_relevance:
                rejected.append({
                    "item": f"{p.company} — {p.title}",
                    "reason": f"low relevance {p.relevance_score:.0%} "
                              f"< {inputs.min_relevance:.0%} "
                              f"({_weakest(p.score_breakdown)})",
                })
                continue
            if p.ghost_score > inputs.max_ghost:
                rejected.append({
                    "item": f"{p.company} — {p.title}",
                    "reason": f"ghost-job signal {p.ghost_score:.0%}",
                })
                continue
            ranked.append(p)

        # Sort by relevance desc, then by recency desc.
        ranked.sort(
            key=lambda p: (p.relevance_score, p.posted_at or 0.0), reverse=True
        )
        kept = len(ranked)
        coverage = kept / max(1, before)
        self.emit(
            trace, "act",
            f"kept {kept} of {before} postings after relevance/ghost filtering",
            considered=[s.name for s in inputs.sources],
            rejected=rejected[:8],
            confidence=round(coverage, 3),
        )

        return DiscoveryBatch(
            batch_id=uuid.uuid4().hex,
            plan_id=inputs.plan_id,
            postings=ranked,
            sources_used=sources_used,
            degraded_sources=degraded,
        )

    def critique(
        self,
        inputs: DiscoveryInputs,
        output: DiscoveryBatch,
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        target = max(1, inputs.weekly_target * 3)  # plan asks for 3× target
        size_score = min(1.0, len(output.postings) / target)
        avg_rel = (
            sum(p.relevance_score for p in output.postings) / len(output.postings)
            if output.postings
            else 0.0
        )
        coverage = (len(output.sources_used) - len(output.degraded_sources)) / max(
            1, len(output.sources_used)
        )
        return {
            "size_vs_target": round(size_score, 3),
            # No rescaling: the score is a real 0..1 fit now, so the agent's
            # quality gate and the number shown to the user are finally the same
            # quantity. This used to be `avg_rel * 4` to compensate for a cosine
            # that could not exceed ~0.15.
            "avg_relevance": round(min(1.0, avg_rel), 3),
            "source_coverage": round(coverage, 3),
        }

    def decide(
        self,
        inputs: DiscoveryInputs,
        output: DiscoveryBatch,
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        decision = (
            f"discovered {len(output.postings)} ranked postings from "
            f"{len(output.sources_used)} sources "
            f"({len(output.degraded_sources)} degraded)"
        )
        return decision, avg
