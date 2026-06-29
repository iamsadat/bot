"""Company Vetting & Research Agent (Phase-0 implementation).

This iteration scores companies from signals available on the JobPosting
(salary band as a comp signal, posting age as activity, location/remote
fit, JD specificity). Phase 4 swaps in Glassdoor/Crunchbase/news/layoffs
data behind the same scorecard interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jobhunt.agents.base import BaseAgent
from jobhunt.models import (
    DiscoveryBatch,
    JobPosting,
    ReasoningTrace,
    RiskRewardScorecard,
    UserProfile,
)

if TYPE_CHECKING:
    pass


# Default weights used when caller does not supply custom weights.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "compensation": 0.35,
    "freshness": 0.20,
    "specificity": 0.25,
    "location_fit": 0.20,
}

# Small default weight given to each enricher metric not explicitly listed in
# caller-supplied weights (re-normalisation happens afterward).
_ENRICHER_DEFAULT_WEIGHT = 0.05

# Metrics that should be inverted (high value = bad) before applying weight.
_INVERT_METRICS = {"recent_layoff_risk"}


@dataclass
class VettingInputs:
    profile: UserProfile
    batch: DiscoveryBatch
    threshold: float = 0.5
    # Optional enrichers — default empty so existing call-sites are unchanged.
    enrichers: list = field(default_factory=list)  # list[Enricher]
    # Optional user-supplied weights dict. None → use hard-coded defaults.
    weights: dict[str, float] | None = None


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
        passed: list[str] = []
        rejected: list[dict[str, str]] = []
        for company, posting in seen.items():
            score, rationale = self._score_posting(posting, inputs)
            ok = score >= inputs.threshold
            cards.append(
                RiskRewardScorecard(
                    company_id=company,
                    score=round(score, 3),
                    rationale=rationale,
                    pass_threshold=ok,
                )
            )
            if ok:
                passed.append(company)
            else:
                rejected.append({
                    "item": company,
                    "reason": f"weighted score {score:.0%} < {inputs.threshold:.0%}",
                })
        self.emit(
            trace, "act",
            f"vetted {len(cards)} companies; {len(passed)} passed threshold",
            considered=passed[:8],
            rejected=rejected[:8],
            confidence=round(len(passed) / max(1, len(cards)), 3),
        )
        return cards

    # ----- scoring entry-point ----------------------------------------------

    def _score_posting(
        self, posting: JobPosting, inputs: VettingInputs
    ) -> tuple[float, dict[str, str]]:
        """Return (weighted_score, rationale_dict) for a single posting."""

        # --- base signals ---------------------------------------------------
        comp = self._comp_score(posting, inputs.profile)
        fresh = self._freshness_score(posting)
        specificity = self._specificity_score(posting)
        loc_fit = self._location_score(posting, inputs.profile)

        components: dict[str, float] = {
            "compensation": comp,
            "freshness": fresh,
            "specificity": specificity,
            "location_fit": loc_fit,
        }

        rationale: dict[str, str] = {
            "compensation": (
                f"min={posting.salary_min}, max={posting.salary_max}, "
                f"profile_min={inputs.profile.min_salary}"
            ),
            "freshness": f"posted_at={posting.posted_at}",
            "specificity": f"jd_len={len(posting.jd_text)}",
            "location_fit": f"remote={posting.remote}, loc={posting.location}",
        }

        # --- enricher signals -----------------------------------------------
        if inputs.enrichers:
            for enricher in inputs.enrichers:
                signals = enricher.enrich(posting)
                for sig in signals:
                    key = f"{enricher.name}.{sig.metric}"
                    # Invert signals where high value = bad.
                    value = (1.0 - sig.value) if sig.metric in _INVERT_METRICS else sig.value
                    components[key] = value
                    invert_note = " [inverted]" if sig.metric in _INVERT_METRICS else ""
                    rationale[key] = f"{sig.detail}{invert_note}"

        # --- build / normalise weight vector --------------------------------
        weights = self._build_weights(inputs.weights, components)

        # --- weighted sum ---------------------------------------------------
        score = sum(weights[k] * components[k] for k in components)
        return score, rationale

    @staticmethod
    def _build_weights(
        user_weights: dict[str, float] | None,
        components: dict[str, float],
    ) -> dict[str, float]:
        """Return a weight dict normalised to sum to 1.0.

        When *user_weights* is None and there are no enricher components,
        the result is exactly the legacy hard-coded weights so that existing
        tests continue to produce identical scores.
        """
        base_keys = {"compensation", "freshness", "specificity", "location_fit"}
        enricher_keys = [k for k in components if k not in base_keys]

        if user_weights is None and not enricher_keys:
            # Exact legacy behaviour — return a copy of the default dict.
            return dict(_DEFAULT_WEIGHTS)

        # Start from user-supplied weights or the defaults.
        if user_weights is not None:
            w: dict[str, float] = dict(user_weights)
        else:
            w = dict(_DEFAULT_WEIGHTS)

        # Assign default small weight to enricher metrics not in user_weights.
        for key in enricher_keys:
            if key not in w:
                w[key] = _ENRICHER_DEFAULT_WEIGHT

        # Drop any keys not in components (user may have supplied extras).
        w = {k: v for k, v in w.items() if k in components}

        # Ensure all component keys have a weight (fill missing with 0).
        for key in components:
            w.setdefault(key, 0.0)

        # Normalise so weights sum to 1.0.
        total = sum(w.values())
        if total <= 0:
            # Fallback: uniform weights.
            n = len(components)
            return {k: 1.0 / n for k in components}

        return {k: v / total for k, v in w.items()}

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
