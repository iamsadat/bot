"""Orchestrator / Supervisor Agent.

Drives the plan-and-execute loop:

1. Call the StrategyAgent to produce a JobHuntPlan.
2. Walk the plan graph, dispatching each step to the named agent.
3. After each step, decide whether to re-plan (e.g., zero discoveries
   means widen queries; degraded sources means try again later).
4. Persist a parent ReasoningTrace that links to every child trace.

The Orchestrator is intentionally agnostic about which agent is which:
agents are registered by name and invoked via a uniform ``.run(...)``
contract. This is what lets us swap stubs for real implementations
without touching the supervisor.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from jobhunt.adapters.base import JobSource
from jobhunt.agents.base import AgentResult, BaseAgent
from jobhunt.agents.discovery import DiscoveryAgent, DiscoveryInputs
from jobhunt.agents.improvement import ImprovementAgent, ImprovementInputs
from jobhunt.agents.resume import ResumeArchitectAgent, ResumeInputs
from jobhunt.agents.strategy import StrategyAgent, StrategyInputs
from jobhunt.agents.submission import SubmissionAgent, SubmissionInputs
from jobhunt.agents.tracking import TrackingAgent, TrackingInputs
from jobhunt.agents.vetting import VettingAgent, VettingInputs
from jobhunt.models import (
    DiscoveryBatch,
    JobHuntPlan,
    PlanStep,
    ReasoningTrace,
    UserProfile,
)
from jobhunt.trace import ThoughtBus, TraceStore


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class OrchestratorInputs:
    profile: UserProfile
    sources: list[JobSource]
    # How many postings to tailor resumes for per run (was a hardcoded 3).
    shortlist_cap: int = field(default_factory=lambda: _env_int("JOBHUNT_SHORTLIST_CAP", 10))
    # Optional override for the vetting pass threshold (None → agent default 0.5).
    vetting_threshold: float | None = None


@dataclass
class OrchestratorOutput:
    plan: JobHuntPlan
    results: dict[str, Any] = field(default_factory=dict)
    children: dict[str, str] = field(default_factory=dict)  # step_id -> trace_id


class Orchestrator(BaseAgent[OrchestratorInputs, OrchestratorOutput]):
    """Top-level supervisor."""

    name = "orchestrator"
    quality_threshold = 0.6
    max_refinements = 0

    def __init__(
        self,
        trace_store: TraceStore,
        bus: ThoughtBus,
        strategy: StrategyAgent | None = None,
        discovery: DiscoveryAgent | None = None,
        vetting: VettingAgent | None = None,
        resume: ResumeArchitectAgent | None = None,
        submission: SubmissionAgent | None = None,
        tracking: TrackingAgent | None = None,
        improvement: ImprovementAgent | None = None,
        *,
        llm: Callable[[str, dict], str] | None = None,
    ) -> None:
        super().__init__(trace_store, bus)
        self.strategy = strategy or StrategyAgent(trace_store, bus)
        self.discovery = discovery or DiscoveryAgent(trace_store, bus)
        self.vetting = vetting or VettingAgent(trace_store, bus)
        self.resume = resume or ResumeArchitectAgent(trace_store, bus, llm=llm)
        self.submission = submission or SubmissionAgent(trace_store, bus)
        self.tracking = tracking or TrackingAgent(trace_store, bus)
        self.improvement = improvement or ImprovementAgent(trace_store, bus)

    # ----- BaseAgent hooks --------------------------------------------------

    def deliberate(
        self, inputs: OrchestratorInputs, trace: ReasoningTrace
    ) -> list[str]:
        return [
            f"goal: run a full plan-and-execute cycle for "
            f"user={inputs.profile.user_id}.",
            "phase 1: ask the strategy agent for a typed execution graph.",
            "phase 2: walk the graph step-by-step; dispatch to named agents.",
            "phase 3: between steps, reflect on results; re-plan if the "
            "discovery batch is empty or all sources degraded.",
        ]

    def act(
        self, inputs: OrchestratorInputs, trace: ReasoningTrace
    ) -> OrchestratorOutput:
        task_id = uuid.uuid4().hex

        # ---- Phase 1: Strategy ------------------------------------------
        s_result = self.strategy.run(
            StrategyInputs(profile=inputs.profile),
            task_id=task_id,
            parent_trace=trace.trace_id,
        )
        plan = s_result.output
        assert plan is not None
        self.think(
            trace,
            f"plan v{plan.version} accepted with {len(plan.steps)} steps "
            f"(strategy confidence={s_result.trace.confidence:.2f}).",
        )

        out = OrchestratorOutput(plan=plan)
        out.children[s_result.trace.agent] = s_result.trace.trace_id

        # ---- Phase 2: walk graph ---------------------------------------
        while True:
            step = plan.next_runnable()
            if step is None:
                break
            step.status = "running"
            self.think(trace, f"dispatching step {step.step_id} → {step.agent}.")
            try:
                self._run_step(inputs, plan, step, out, task_id, trace)
                step.status = "done"
            except Exception as exc:  # pragma: no cover - defensive
                step.status = "failed"
                self.think(trace, f"step {step.step_id} failed: {exc!r}.")
                break

            # ---- Phase 3: reflect + re-plan --------------------------------
            if step.agent == "discovery":
                batch: DiscoveryBatch = out.results["discovery"]
                if not batch.postings:
                    self.think(
                        trace,
                        "discovery returned 0 postings — re-planning by "
                        "widening role queries (drop location filter).",
                    )
                    self._widen_discovery(plan)

        # Improvement agent always runs at the end as a meta-observer.
        imp_result = self.improvement.run(
            ImprovementInputs(plan=plan, results=out.results),
            task_id=task_id,
            parent_trace=trace.trace_id,
        )
        out.children[imp_result.trace.agent] = imp_result.trace.trace_id

        return out

    def critique(
        self,
        inputs: OrchestratorInputs,
        output: OrchestratorOutput,
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        steps = output.plan.steps
        completed = sum(1 for s in steps if s.status == "done")
        completion = completed / max(1, len(steps))
        batch = output.results.get("discovery")
        coverage = 0.0
        if batch is not None:
            coverage = (
                (len(batch.sources_used) - len(batch.degraded_sources))
                / max(1, len(batch.sources_used))
            )
        return {"plan_completion": completion, "source_coverage": coverage}

    def decide(
        self,
        inputs: OrchestratorInputs,
        output: OrchestratorOutput,
        scores: dict[str, float],
        trace: ReasoningTrace,
    ) -> tuple[str, float]:
        avg = sum(scores.values()) / len(scores) if scores else 0.0
        return f"cycle complete with {len(output.results)} stage outputs", avg

    # ----- helpers ----------------------------------------------------------

    def _run_step(
        self,
        inputs: OrchestratorInputs,
        plan: JobHuntPlan,
        step: PlanStep,
        out: OrchestratorOutput,
        task_id: str,
        parent: ReasoningTrace,
    ) -> None:
        if step.agent == "discovery":
            result: AgentResult = self.discovery.run(
                DiscoveryInputs(
                    profile=inputs.profile,
                    queries=step.inputs["queries"],
                    sources=inputs.sources,
                    plan_id=plan.plan_id,
                    weekly_target=step.inputs.get("weekly_target", 10),
                ),
                task_id=task_id,
                parent_trace=parent.trace_id,
            )
            out.results["discovery"] = result.output
        elif step.agent == "vetting":
            batch: DiscoveryBatch = out.results["discovery"]
            vetting_inputs = VettingInputs(profile=inputs.profile, batch=batch)
            if inputs.vetting_threshold is not None:
                vetting_inputs.threshold = inputs.vetting_threshold
            result = self.vetting.run(
                vetting_inputs,
                task_id=task_id,
                parent_trace=parent.trace_id,
            )
            out.results["vetting"] = result.output
        elif step.agent == "resume":
            batch = out.results["discovery"]
            vetted = out.results.get("vetting", [])
            cap = max(1, inputs.shortlist_cap)
            allowed_companies = {s.company_id for s in vetted if s.pass_threshold}
            shortlisted = (
                [p for p in batch.postings if p.company in allowed_companies][:cap]
                or batch.postings[:cap]
            )
            result = self.resume.run(
                ResumeInputs(profile=inputs.profile, postings=shortlisted),
                task_id=task_id,
                parent_trace=parent.trace_id,
            )
            out.results["resume"] = result.output
        elif step.agent == "submission":
            documents = out.results.get("resume", [])
            result = self.submission.run(
                SubmissionInputs(profile=inputs.profile, documents=documents),
                task_id=task_id,
                parent_trace=parent.trace_id,
            )
            out.results["submission"] = result.output
        elif step.agent == "tracking":
            result = self.tracking.run(
                TrackingInputs(profile=inputs.profile),
                task_id=task_id,
                parent_trace=parent.trace_id,
            )
            out.results["tracking"] = result.output
        else:  # pragma: no cover
            raise ValueError(f"unknown agent in plan: {step.agent}")

        out.children[step.step_id] = result.trace.trace_id
        step.result_ref = result.trace.trace_id

    def _widen_discovery(self, plan: JobHuntPlan) -> None:
        for step in plan.steps:
            if step.agent == "discovery" and step.status == "done":
                for q in step.inputs.get("queries", []):
                    q["location"] = ""
                step.status = "pending"
                plan.version += 1
                return
