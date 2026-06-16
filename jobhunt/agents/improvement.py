"""Continuous Improvement Meta-Agent (Phase-0 implementation).

Observes the cycle's outputs and emits suggested adjustments to other
agents' parameters (e.g. relevance threshold, pass threshold). Phase 4
turns this into an online learning loop with A/B testing and rollback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jobhunt.agents.base import BaseAgent
from jobhunt.models import JobHuntPlan, ReasoningTrace

if TYPE_CHECKING:
    from jobhunt.ab import ExperimentRegistry


@dataclass
class ImprovementInputs:
    plan: JobHuntPlan
    results: dict[str, Any]
    # Phase 4 additions — backward-compatible (default None / empty list)
    action_log: list[dict] = field(default_factory=list)
    """Past cycle records.

    Each entry:  {cycle_id, plan_version, suggestions, applied: list[str],
                  outcome_score: float}
    """
    experiments: "ExperimentRegistry | None" = None
    """Optional experiment registry for A/B tracking and rollback."""


@dataclass
class ImprovementOutput:
    suggestions: list[dict]
    cycle_record: dict = field(default_factory=dict)
    """Snapshot of this cycle appended to the action log."""


class ImprovementAgent(BaseAgent[ImprovementInputs, ImprovementOutput]):
    name = "improvement"
    quality_threshold = 0.5

    def deliberate(
        self, inputs: ImprovementInputs, trace: ReasoningTrace
    ) -> list[str]:
        return [
            "observing the just-completed cycle to infer prompt/parameter "
            "tweaks for next run.",
            "inputs: discovery batch size, vetting pass rate, resume "
            "keyword coverage, sources degraded.",
        ]

    def act(
        self, inputs: ImprovementInputs, trace: ReasoningTrace
    ) -> ImprovementOutput:
        suggestions: list[dict] = []
        batch = inputs.results.get("discovery")
        if batch is not None:
            if len(batch.postings) < 5:
                suggestions.append(
                    {
                        "target": "discovery",
                        "change": "lower min_relevance by 0.02",
                        "reason": f"only {len(batch.postings)} postings survived filters",
                    }
                )
            if batch.degraded_sources:
                suggestions.append(
                    {
                        "target": "discovery",
                        "change": "back off and retry degraded sources next cycle",
                        "reason": f"degraded: {batch.degraded_sources}",
                    }
                )
        vetting = inputs.results.get("vetting")
        if vetting:
            pass_rate = sum(1 for c in vetting if c.pass_threshold) / len(vetting)
            if pass_rate < 0.2:
                suggestions.append(
                    {
                        "target": "vetting",
                        "change": "loosen threshold by 0.05",
                        "reason": f"pass_rate={pass_rate:.2f} too restrictive",
                    }
                )
        resume = inputs.results.get("resume")
        if resume:
            avg_cov = sum(d.keyword_coverage for d in resume) / len(resume)
            if avg_cov < 0.5:
                suggestions.append(
                    {
                        "target": "resume",
                        "change": "expand evidence graph extraction",
                        "reason": f"avg ATS coverage {avg_cov:.2f} below 0.5",
                    }
                )

        # Phase 4: A/B experiment integration (only when registry is supplied)
        if inputs.experiments is not None:
            for exp in inputs.experiments.all():
                if exp.should_rollback():
                    suggestions.append(
                        {
                            "target": exp.target,
                            "change": f"rollback experiment '{exp.name}' to control",
                            "reason": (
                                "non-control variant success_rate fell below "
                                f"{exp.rollback_threshold:.0%} of control's rate"
                            ),
                        }
                    )
                winner = exp.winner()
                if winner is not None:
                    suggestions.append(
                        {
                            "target": exp.target,
                            "change": f"promote_winner '{winner.name}' for experiment '{exp.name}'",
                            "reason": (
                                f"variant '{winner.name}' outperforms control by ≥10% "
                                f"(rate={winner.success_rate:.2f})"
                            ),
                        }
                    )

        # Build cycle snapshot
        cycle_record: dict = {
            "cycle_id": inputs.plan.plan_id,
            "plan_version": inputs.plan.version,
            "suggestions": list(suggestions),
        }

        return ImprovementOutput(suggestions=suggestions, cycle_record=cycle_record)

    def critique(
        self,
        inputs: ImprovementInputs,
        output: ImprovementOutput,
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        # We always produce *something*; quality is whether suggestions
        # are explainable.
        explainable = all("reason" in s for s in output.suggestions) if output.suggestions else True
        return {"explainability": 1.0 if explainable else 0.0}
