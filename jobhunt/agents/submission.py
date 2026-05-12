"""Application & Submission Agent (Phase-0 dry-run).

In the MVP this prepares submission *packages* without contacting any
job board — it's the strategy and reasoning surface that needs to exist
for the Orchestrator graph to compile. Phase 3 swaps the ``_route``
function for real Greenhouse/Lever API clients and a Playwright auto-fill
driver. The output schema (SubmissionPlan) does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.agents.base import BaseAgent
from jobhunt.agents.resume import TailoredDocument
from jobhunt.models import ReasoningTrace, UserProfile


@dataclass
class SubmissionPlan:
    job_id: str
    company: str
    route: str  # "api" | "autofill" | "email" | "manual"
    package: dict
    requires_user_click: bool
    notes: str = ""


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
            plans.append(
                SubmissionPlan(
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
            )
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
        return {"routed": round(routed, 3), "coverage": coverage}
