"""Application & Submission Agent (Phase-0 dry-run / Phase-3 auto-submit).

In the MVP this prepares submission *packages* without contacting any
job board — it's the strategy and reasoning surface that needs to exist
for the Orchestrator graph to compile.

Phase 3 (current): when a :class:`~jobhunt.submitters.SubmitterRegistry`
is injected and ``inputs.auto_submit_approved`` is ``True``, the agent
actually POSTs to the Greenhouse / Lever APIs via the registry.  All
network calls go through an injectable :class:`~jobhunt.submitters.Poster`
so tests remain fully offline.

The output schema (``SubmissionPlan``) is backward-compatible: new fields
``submitted`` and ``submission_id`` default to ``False`` / ``""``
respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jobhunt.agents.base import BaseAgent
from jobhunt.agents.resume import TailoredDocument
from jobhunt.models import ReasoningTrace, UserProfile
from jobhunt.trace import ThoughtBus, TraceStore
from jobhunt.tools import ToolRegistry

if TYPE_CHECKING:
    from jobhunt.submitters.registry import SubmitterRegistry


@dataclass
class SubmissionPlan:
    job_id: str
    company: str
    route: str  # "api" | "autofill" | "email" | "manual"
    package: dict
    requires_user_click: bool
    notes: str = ""
    # Phase-3 fields (default-False / empty for backward compat)
    submitted: bool = False
    submission_id: str = ""


@dataclass
class SubmissionInputs:
    profile: UserProfile
    documents: list[TailoredDocument]
    auto_submit_approved: bool = False  # user must opt in explicitly


_KNOWN_ATS = {
    "boards.greenhouse.io": "api",
    "jobs.lever.co": "api",
    "jobs.ashbyhq.com": "api",
    "myworkdayjobs.com": "autofill",
    "icims.com": "autofill",
}


def _route(url: str) -> str:
    for domain, kind in _KNOWN_ATS.items():
        if domain in url:
            return kind
    return "manual"


class SubmissionAgent(BaseAgent[SubmissionInputs, list[SubmissionPlan]]):
    name = "submission"
    quality_threshold = 0.7

    def __init__(
        self,
        trace_store: TraceStore,
        bus: ThoughtBus,
        tools: ToolRegistry | None = None,
        registry: "SubmitterRegistry | None" = None,
    ) -> None:
        super().__init__(trace_store, bus, tools)
        self.registry = registry

    def deliberate(self, inputs: SubmissionInputs, trace: ReasoningTrace) -> list[str]:
        return [
            f"preparing submission packages for {len(inputs.documents)} "
            "approved documents.",
            "plan: route each application via known ATS (Greenhouse/Lever/"
            "Ashby/Workday/iCIMS) or fall back to manual one-click assist.",
            f"auto-submit allowed: {inputs.auto_submit_approved}.",
        ]

    def act(
        self, inputs: SubmissionInputs, trace: ReasoningTrace
    ) -> list[SubmissionPlan]:
        plans: list[SubmissionPlan] = []
        for doc in inputs.documents:
            route = _route(doc.url.lower())
            package = {
                "resume": doc.resume_text,
                "cover_letter": doc.cover_letter_text,
                "applicant": {
                    "name": inputs.profile.name,
                    "email": inputs.profile.email,
                },
                "matched_keywords": doc.matched_keywords,
            }
            plan = SubmissionPlan(
                job_id=doc.job_id,
                company=doc.company,
                route=route,
                package=package,
                requires_user_click=not inputs.auto_submit_approved,
                notes=(
                    f"route={route}; coverage={doc.keyword_coverage}; "
                    f"missing={doc.missing_keywords[:3]}"
                ),
            )

            # Phase-3: attempt auto-submit when conditions are met.
            if (
                inputs.auto_submit_approved
                and route == "api"
                and self.registry is not None
            ):
                submit_plan = {
                    "url": doc.url,
                    "job_id": doc.job_id,
                    "applicant": {
                        "name": inputs.profile.name,
                        "email": inputs.profile.email,
                        "phone": getattr(inputs.profile, "phone", ""),
                    },
                    "resume_text": doc.resume_text,
                    "cover_letter_text": doc.cover_letter_text,
                }
                result = self.registry.submit(submit_plan)
                if result is not None:
                    if result.ok:
                        plan.submitted = True
                        plan.submission_id = result.submission_id
                        plan.notes += f"; auto-submitted ok (id={result.submission_id})"
                    else:
                        plan.submitted = False
                        plan.notes += f"; auto-submit failed: {result.detail}"

            plans.append(plan)
        return plans

    def critique(
        self,
        inputs: SubmissionInputs,
        output: list[SubmissionPlan],
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        if not output:
            return {"routed": 0.0, "coverage": 0.0}
        routed = sum(1 for p in output if p.route != "manual") / len(output)
        coverage = 1.0  # every doc was routed (even if to manual)
        metrics: dict[str, float] = {"routed": round(routed, 3), "coverage": coverage}

        if self.registry is not None:
            api_plans = [p for p in output if p.route == "api"]
            if api_plans:
                submitted = sum(1 for p in api_plans if p.submitted)
                metrics["submission_success"] = round(submitted / len(api_plans), 3)

        return metrics
