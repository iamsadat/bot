"""Strategy & Planning Agent.

Converts a UserProfile into a JobHuntPlan (the typed execution graph
the Orchestrator follows). Reasoning is rule-based here so the MVP runs
without an LLM — the contract is identical to the production version
that delegates the deliberation to Claude.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from jobhunt.agents.base import BaseAgent
from jobhunt.models import JobHuntPlan, PlanStep, ReasoningTrace, UserProfile


@dataclass
class StrategyInputs:
    profile: UserProfile


def _new_step_id() -> str:
    return uuid.uuid4().hex[:8]


class StrategyAgent(BaseAgent[StrategyInputs, JobHuntPlan]):
    name = "strategy"
    quality_threshold = 0.75

    def deliberate(self, inputs: StrategyInputs, trace: ReasoningTrace) -> list[str]:
        p = inputs.profile
        bullets = [
            f"profile: roles={p.target_roles}, locations={p.locations}, "
            f"remote_ok={p.remote_ok}, weekly_target={p.weekly_target}",
            "step 1: reason about role alignment — which target roles map to "
            "the user's existing skills vs. stretch roles.",
            "step 2: decide source priorities — Greenhouse/Lever/Ashby give "
            "high-signal listings; LinkedIn/Indeed give breadth.",
            "step 3: pick query strategy — combine role title with top 3 "
            "skills, exclude veto companies.",
            "step 4: choose stop conditions — weekly target hit OR no new "
            "relevant postings for 3 days.",
        ]
        return bullets

    def act(self, inputs: StrategyInputs, trace: ReasoningTrace) -> JobHuntPlan:
        p = inputs.profile

        # Build per-role search queries with the user's top skills appended.
        top_skills = p.skills[:3]
        queries: list[dict] = []
        for role in p.target_roles:
            for loc in p.locations or [""]:
                queries.append(
                    {
                        "role": role,
                        "location": loc,
                        "remote_ok": p.remote_ok,
                        "skills": top_skills,
                        "exclude_companies": p.veto_companies,
                    }
                )

        # Build the execution graph.
        plan_id = uuid.uuid4().hex
        s_discover = PlanStep(
            step_id=_new_step_id(),
            agent="discovery",
            action="discover_jobs",
            inputs={"queries": queries, "weekly_target": p.weekly_target},
        )
        s_vet = PlanStep(
            step_id=_new_step_id(),
            agent="vetting",
            action="score_companies",
            inputs={"from_batch": "${discovery}"},
            depends_on=[s_discover.step_id],
        )
        s_resume = PlanStep(
            step_id=_new_step_id(),
            agent="resume",
            action="tailor_documents",
            inputs={"from_vetted": "${vetting}"},
            depends_on=[s_vet.step_id],
        )
        s_submit = PlanStep(
            step_id=_new_step_id(),
            agent="submission",
            action="prepare_submissions",
            inputs={"from_resume": "${resume}"},
            depends_on=[s_resume.step_id],
        )
        s_track = PlanStep(
            step_id=_new_step_id(),
            agent="tracking",
            action="watch_pipeline",
            inputs={},
            depends_on=[s_submit.step_id],
        )

        plan = JobHuntPlan(
            plan_id=plan_id,
            user_id=p.user_id,
            milestones=[
                "discover ≥ 3× weekly_target candidates",
                "vet candidates and drop sub-threshold companies",
                "tailor + human-approve top-N documents",
                "submit and start tracking pipeline",
            ],
            steps=[s_discover, s_vet, s_resume, s_submit, s_track],
            notes=f"queries={len(queries)}, top_skills={top_skills}",
        )
        return plan

    def critique(
        self, inputs: StrategyInputs, output: JobHuntPlan, trace: ReasoningTrace
    ) -> dict[str, float]:
        p = inputs.profile
        coverage = 1.0 if output.steps and len(output.steps) >= 5 else 0.4
        # Did we generate a query per role?
        discover_step = next(s for s in output.steps if s.agent == "discovery")
        roles_covered = {q["role"] for q in discover_step.inputs["queries"]}
        role_coverage = (
            len(roles_covered & set(p.target_roles)) / max(1, len(p.target_roles))
        )
        realism = 0.9 if p.weekly_target <= 25 else 0.5
        return {
            "step_coverage": coverage,
            "role_coverage": role_coverage,
            "realism": realism,
        }

    def decide(
        self,
        inputs: StrategyInputs,
        output: JobHuntPlan,
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        return (f"plan_v{output.version} with {len(output.steps)} steps", avg)
