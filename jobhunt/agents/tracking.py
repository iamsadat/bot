"""Progress Tracking & Communication Agent (Phase-0 implementation).

In the MVP this exposes a classifier and pipeline-mover that runs over
in-memory ``EmailEvent`` records. Phase 3 wires this into an IMAP/Gmail
watcher and Google Calendar; the classifier and pipeline transitions
stay identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.agents.base import BaseAgent
from jobhunt.models import Application, ApplicationStatus, ReasoningTrace, UserProfile


@dataclass
class TrackingInputs:
    profile: UserProfile
    inbox: list[dict] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)


@dataclass
class TrackingOutput:
    transitions: list[dict]
    applications: list[Application]


_REJECT_HINTS = (
    "unfortunately",
    "moved forward with other candidates",
    "we will not be moving",
    "not be progressing",
    "decided to move forward with other",
)
_INTERVIEW_HINTS = (
    "schedule a call",
    "interview",
    "set up time",
    "calendly",
    "zoom link",
)
_ASSESSMENT_HINTS = ("take-home", "coding challenge", "assessment", "hackerrank")
_OFFER_HINTS = ("offer letter", "we are excited to extend", "offer of employment")


def classify(text: str) -> str:
    t = text.lower()
    if any(h in t for h in _OFFER_HINTS):
        return "offer"
    if any(h in t for h in _REJECT_HINTS):
        return "rejection"
    if any(h in t for h in _ASSESSMENT_HINTS):
        return "assessment"
    if any(h in t for h in _INTERVIEW_HINTS):
        return "interview"
    return "other"


_STATUS_FROM_LABEL = {
    "rejection": ApplicationStatus.CLOSED,
    "assessment": ApplicationStatus.ASSESSMENT,
    "interview": ApplicationStatus.INTERVIEW,
    "offer": ApplicationStatus.OFFER,
}


class TrackingAgent(BaseAgent[TrackingInputs, TrackingOutput]):
    name = "tracking"
    quality_threshold = 0.6

    def deliberate(self, inputs: TrackingInputs, trace: ReasoningTrace) -> list[str]:
        return [
            f"scanning {len(inputs.inbox)} inbound messages against "
            f"{len(inputs.applications)} open applications.",
            "plan: classify each message; resolve to ApplicationId via "
            "company match; move pipeline state forward.",
        ]

    def act(self, inputs: TrackingInputs, trace: ReasoningTrace) -> TrackingOutput:
        by_company: dict[str, Application] = {}
        for app in inputs.applications:
            by_company[app.job_id] = app  # placeholder match by id

        transitions: list[dict] = []
        for msg in inputs.inbox:
            label = classify(msg.get("body", ""))
            target_company = (msg.get("company") or "").lower()
            app = next(
                (
                    a
                    for a in inputs.applications
                    if target_company and target_company in a.job_id.lower()
                ),
                None,
            )
            if app is None or label == "other":
                continue
            new_status = _STATUS_FROM_LABEL[label]
            if app.status != new_status:
                transitions.append(
                    {
                        "application_id": app.application_id,
                        "from": app.status,
                        "to": new_status,
                        "evidence_subject": msg.get("subject", ""),
                    }
                )
                app.status = new_status

        return TrackingOutput(transitions=transitions, applications=inputs.applications)

    def critique(
        self,
        inputs: TrackingInputs,
        output: TrackingOutput,
        trace: ReasoningTrace,
    ) -> dict[str, float]:
        coverage = 1.0 if not inputs.inbox else (
            len(output.transitions) / len(inputs.inbox)
        )
        return {"coverage": round(min(1.0, coverage), 3)}
