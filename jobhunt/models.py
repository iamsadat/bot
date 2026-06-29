"""Typed data models for the JobHunt platform.

Stdlib-only (dataclasses) so the MVP runs in any environment. The fields
mirror the production schema in ARCHITECTURE.md §4; swap to Pydantic +
SQLAlchemy when wiring Postgres in Phase 1.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------- user

@dataclass
class Experience:
    """One structured work-history entry, used to render a real resume."""

    title: str = ""
    company: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


@dataclass
class Education:
    school: str = ""
    degree: str = ""
    field: str = ""
    start: str = ""
    end: str = ""
    location: str = ""


@dataclass
class Project:
    name: str = ""
    description: str = ""
    bullets: list[str] = field(default_factory=list)
    link: str = ""
    skills: list[str] = field(default_factory=list)


def _coerce(cls: type, item: Any) -> Any:
    """Coerce a (possibly partial / legacy free-form) dict into ``cls``.

    Tolerates missing keys and ignores unknown ones so old snapshots and
    hand-written experience dicts keep loading after the schema grew.
    """
    if isinstance(item, cls):
        return item
    if not isinstance(item, dict):
        return cls()
    fields = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in item.items() if k in fields})


@dataclass
class UserProfile:
    user_id: str
    name: str
    email: str
    target_roles: list[str]
    locations: list[str]
    phone: str = ""
    min_salary: int | None = None
    remote_ok: bool = True
    culture_keywords: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    experiences: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)
    veto_companies: list[str] = field(default_factory=list)
    weekly_target: int = 10
    # Standard answers to common ATS screening / custom questions, used when
    # auto-submitting (work authorization, sponsorship, years of experience,
    # LinkedIn/website, optional EEO). Free-form so new keys can be added.
    application_answers: dict[str, Any] = field(default_factory=dict)
    # Autonomy controls: when ``auto_apply`` is on and a real ATS is connected,
    # the engine auto-submits matches at/above ``relevance_floor`` up to
    # ``daily_apply_cap`` applications per day. Intent only — never a secret.
    auto_apply: bool = False
    daily_apply_cap: int = 0
    relevance_floor: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # --- structured accessors (storage stays plain dicts for JSON simplicity) -
    def structured_experiences(self) -> list[Experience]:
        return [_coerce(Experience, e) for e in self.experiences]

    def structured_education(self) -> list[Education]:
        return [_coerce(Education, e) for e in self.education]

    def structured_projects(self) -> list[Project]:
        return [_coerce(Project, p) for p in self.projects]


# ----------------------------------------------------------------- planning

@dataclass
class PlanStep:
    step_id: str
    agent: str
    action: str
    inputs: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed | skipped
    result_ref: str | None = None


@dataclass
class JobHuntPlan:
    plan_id: str
    user_id: str
    milestones: list[str]
    steps: list[PlanStep]
    created_at: float = field(default_factory=_now)
    version: int = 1
    notes: str = ""

    def next_runnable(self) -> PlanStep | None:
        done = {s.step_id for s in self.steps if s.status == "done"}
        for s in self.steps:
            if s.status != "pending":
                continue
            if all(dep in done for dep in s.depends_on):
                return s
        return None


# --------------------------------------------------------------- discovery

@dataclass
class Company:
    company_id: str
    name: str
    domain: str | None = None
    glassdoor_rating: float | None = None
    funding_stage: str | None = None
    headcount: int | None = None
    layoffs_12mo: int | None = None
    sentiment: float | None = None  # -1..1
    tech_stack: list[str] = field(default_factory=list)


@dataclass
class JobPosting:
    job_id: str
    source: str
    source_id: str
    url: str
    title: str
    company: str
    location: str
    jd_text: str
    posted_at: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    remote: bool = False
    relevance_score: float = 0.0
    ghost_score: float = 0.0
    fingerprint: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        key = "|".join(
            [
                self.company.strip().lower(),
                self.title.strip().lower(),
                self.location.strip().lower(),
            ]
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class DiscoveryBatch:
    batch_id: str
    plan_id: str
    postings: list[JobPosting]
    sources_used: list[str]
    degraded_sources: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_now)


# ----------------------------------------------------------------- vetting

@dataclass
class RiskRewardScorecard:
    company_id: str
    score: float  # 0..1
    rationale: dict[str, str]  # criterion -> reasoning
    pass_threshold: bool = False


# ------------------------------------------------------------- applications

class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    CLOSED = "closed"


@dataclass
class Application:
    application_id: str
    user_id: str
    job_id: str
    status: ApplicationStatus = ApplicationStatus.SAVED
    submitted_at: float | None = None
    confirmation_id: str | None = None
    documents: list[str] = field(default_factory=list)  # s3 keys


# ----------------------------------------------------------- reasoning trace

@dataclass
class ToolCall:
    tool: str
    args_summary: str
    ok: bool
    latency_ms: int
    retries: int = 0
    fallback_used: bool = False
    error: str | None = None


@dataclass
class TraceEvent:
    """A substantive reasoning step — not just a status line.

    Captures what an agent *considered*, what it *rejected* and why, its
    *confidence*, and the *decision* it reached, so the dashboard can render a
    genuine reasoning feed instead of a flat activity log. ``phase`` is one of
    deliberate | act | critique | decide.
    """

    phase: str
    summary: str
    considered: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)  # {item, reason}
    confidence: float | None = None
    decision: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)


@dataclass
class ReasoningTrace:
    trace_id: str
    agent: str
    task_id: str
    thoughts: list[str]
    tool_calls: list[ToolCall] = field(default_factory=list)
    self_critique: dict[str, float] = field(default_factory=dict)
    decision: str = ""
    confidence: float = 0.0
    parent_trace_id: str | None = None
    events: list[TraceEvent] = field(default_factory=list)
    created_at: float = field(default_factory=_now)

    @staticmethod
    def new(agent: str, task_id: str, parent: str | None = None) -> "ReasoningTrace":
        return ReasoningTrace(
            trace_id=_new_id(),
            agent=agent,
            task_id=task_id,
            thoughts=[],
            parent_trace_id=parent,
        )
