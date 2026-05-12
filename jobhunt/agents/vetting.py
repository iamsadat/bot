"""Company Vetting & Research Agent (Phase-0 implementation).

This iteration scores companies from signals available on the JobPosting
(salary band as a comp signal, posting age as activity, location/remote
fit, JD specificity). Phase 4 swaps in Glassdoor/Crunchbase/news/layoffs
data behind the same scorecard interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from jobhunt.agents.base import BaseAgent
from jobhunt.models import (
    DiscoveryBatch,
    JobPosting,
    ReasoningTrace,
    RiskRewardScorecard,
    UserProfile,
)


@dataclass
class VettingInputs:
    profile: UserProfile
    batch: DiscoveryBatch
    threshold: float = 0.5


class VettingAgent(BaseAgent[VettingInputs, list[RiskRewardScorecard]]):
    name = "vetting"
    quality_threshold = 0.6

    def deliberate(self, inputs: VettingInputs, trace: ReasoningTrace) -> list[str]:
        return [
            f"vetting {len({p.company for p in inputs.batch.postings})} unique "
            "companies from the discovery batch.",
            "criteria: compensation alignment, posting freshness, JD "
            "specificity (proxy for hiring intent), remote/location fit.",
            f"pass threshold: weighted score ≥ {inputs.threshold}.",
        ]

    def act(
        self, inputs: VettingInputs, trace: ReasoningTrace
    ) -> list[RiskRewardScorecard]:
        seen: dict[str, JobPosting] = {}
        for p in inputs.batch.postings:
            seen.setdefault(p.company, p)

        cards: list[RiskRewardScorecard] = []
        for company, posting in seen.items():
            comp = self._comp_score(posting, inputs.profile)
            fresh = self._freshness_score(posting)
            specificity = self._specificity_score(posting)
            loc_fit = self._location_score(posting, inputs.profile)

            weighted = (
                0.35 * comp + 0.20 * fresh + 0.25 * specificity + 0.20 * loc_fit
            )
            cards.append(
                RiskRewardScorecard(
                    company_id=company,
                    score=round(weighted, 3),
                    rationale={
                        "compensation": f"min={posting.salary_min}, max={posting.salary_max}, profile_min={inputs.profile.min_salary}",
                        "freshness": f"posted_at={posting.posted_at}",
                        "specificity": f"jd_len={len(posting.jd_text)}",
                        "location_fit": f"remote={posting.remote}, loc={posting.location}",
                    },
                    pass_threshold=weighted >= inputs.threshold,
                )
            )
        return cards

    def critique(
        self,
        inputs: VettingInputs,
        output: list[RiskRewardScorecard],
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        if not output:
            return {"explainability": 0.0, "pass_rate_sanity": 0.0}
        explainability = 1.0  # every card has a per-criterion rationale
        pass_rate = sum(1 for c in output if c.pass_threshold) / len(output)
        # Sanity: pass_rate between 0.2 and 0.9 is healthy.
        sanity = 1.0 if 0.2 <= pass_rate <= 0.9 else 0.5
        return {"explainability": explainability, "pass_rate_sanity": sanity}

    def decide(
        self,
        inputs: VettingInputs,
        output: list[RiskRewardScorecard],
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        passed = sum(1 for c in output if c.pass_threshold)
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        return f"vetted {len(output)} companies; {passed} passed threshold", avg

    # ----- scoring helpers --------------------------------------------------

    @staticmethod
    def _comp_score(p: JobPosting, profile: UserProfile) -> float:
        if profile.min_salary is None:
            return 0.6
        if p.salary_min is None:
            return 0.5
        if p.salary_min >= profile.min_salary:
            return 1.0
        # Linear falloff to 0 at half the user's minimum.
        gap = (profile.min_salary - p.salary_min) / profile.min_salary
        return max(0.0, 1.0 - 2 * gap)

    @staticmethod
    def _freshness_score(p: JobPosting, now: float | None = None) -> float:
        now = now or time.time()
        if not p.posted_at:
            return 0.5
        age_days = (now - p.posted_at) / 86400
        if age_days <= 14:
            return 1.0
        if age_days <= 30:
            return 0.7
        if age_days <= 60:
            return 0.4
        return 0.1

    @staticmethod
    def _specificity_score(p: JobPosting) -> float:
        n = len(p.jd_text)
        if n >= 400:
            return 1.0
        if n >= 200:
            return 0.7
        if n >= 100:
            return 0.4
        return 0.1

    @staticmethod
    def _location_score(p: JobPosting, profile: UserProfile) -> float:
        if profile.remote_ok and p.remote:
            return 1.0
        if any(loc.lower() in p.location.lower() for loc in profile.locations):
            return 1.0
        return 0.4
