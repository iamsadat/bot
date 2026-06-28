"""Job Discovery & Intelligence Agent.

Plans queries → fans out to source adapters → normalizes → dedupes →
relevance + ghost-job scoring → emits a ranked DiscoveryBatch.

Relevance is a lightweight bag-of-words cosine against the user's
skill vector. In production this becomes pgvector + a real embedding
model; the contract (a 0..1 score) does not change.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass

from jobhunt.adapters.base import JobSource
from jobhunt.agents.base import BaseAgent
from jobhunt.embeddings import cosine_similarity, embed_jd_text, embed_user_skills
from jobhunt.models import (
    DiscoveryBatch,
    JobPosting,
    ReasoningTrace,
    UserProfile,
)
from jobhunt.skills_taxonomy import expand_terms


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]*")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[t] * b[t] for t in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def relevance(posting: JobPosting, profile: UserProfile) -> float:
    """Blended 0..1 relevance of a posting to the user.

    Combines two complementary signals:
      * lexical bag-of-words cosine over **synonym-expanded** skills/roles
        (so "k8s" in the JD counts for a "kubernetes" skill), and
      * semantic cosine in the hashing-trick embedding space (catches related
        vocabulary that doesn't share exact tokens).

    The blend re-ranks borderline jobs more sharply than raw token overlap.
    """
    skill_terms = list(profile.skills) + list(profile.target_roles)
    expanded = expand_terms(skill_terms)
    profile_vec = Counter(_tokenize(" ".join(sorted(expanded))))
    jd_text = posting.jd_text + " " + posting.title
    jd_vec = Counter(_tokenize(jd_text))
    lexical = _cosine(profile_vec, jd_vec)

    semantic = cosine_similarity(
        embed_user_skills(skill_terms), embed_jd_text(jd_text)
    )
    return round(0.55 * lexical + 0.45 * semantic, 4)


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
    min_relevance: float = 0.05
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
                    return source.search(query)

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
        for p in all_postings:
            p.relevance_score = relevance(p, inputs.profile)
            p.ghost_score = ghost_score(p)
            if p.relevance_score < inputs.min_relevance:
                continue
            if p.ghost_score > inputs.max_ghost:
                continue
            ranked.append(p)

        # Sort by relevance desc, then by recency desc.
        ranked.sort(
            key=lambda p: (p.relevance_score, p.posted_at or 0.0), reverse=True
        )
        self.think(trace, f"after relevance/ghost filtering: {len(ranked)}")

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
            "avg_relevance": round(min(1.0, avg_rel * 4), 3),
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
