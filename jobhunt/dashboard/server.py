"""FastAPI dashboard — onboarding + live pipeline.

Endpoints:

  GET  /                          single-page app (client.html)
  GET  /api/status                hunt lifecycle state
  POST /api/onboarding/profile    save user info + preferences
  POST /api/onboarding/resume     parse pasted resume text → extract skills
  POST /api/hunt/start            kick off the background orchestrator
  GET  /api/plan                  current execution plan (steps + statuses)
  GET  /api/jobs                  discovered job postings (Kanban source)
  GET  /api/applications          pipeline applications
  GET  /api/traces                reasoning traces (paginated)
  GET  /api/approvals             approval queue
  POST /api/approve/{id}          human one-click decision
  WS   /ws/stream                 live thought stream
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jobhunt.approval import ApprovalQueue, ApprovalState, InvalidTransition
from jobhunt.models import JobHuntPlan, UserProfile
from jobhunt.onboarding import build_user_profile, parse_resume_text
from jobhunt.trace import ThoughtBus, TraceStore


# ---------------------------------------------------------------------------
# Shared state container
# ---------------------------------------------------------------------------

@dataclass
class DashboardState:
    trace_store: TraceStore
    bus: ThoughtBus
    plan: JobHuntPlan | None = None
    jobs: list[dict] = field(default_factory=list)
    applications: list[dict] = field(default_factory=list)
    approval_queue: ApprovalQueue = field(default_factory=ApprovalQueue)
    user_profile: UserProfile | None = None
    # hunt lifecycle: idle | running | complete | failed
    hunt_status: str = "idle"
    hunt_error: str = ""
    hunt_progress: dict[str, str] = field(default_factory=dict)  # step_id → status


# ---------------------------------------------------------------------------
# Background hunt runner
# ---------------------------------------------------------------------------

def _execute_hunt(state: DashboardState) -> None:
    """Runs the full orchestrator pipeline synchronously (called in a thread)."""
    from jobhunt.adapters.fixture import FixtureSource
    from jobhunt.agents.orchestrator import Orchestrator, OrchestratorInputs

    sources = [
        FixtureSource(name="greenhouse", only_sources=["greenhouse", "ashby", "lever"]),
        FixtureSource(name="linkedin", only_sources=["linkedin"]),
        FixtureSource(name="indeed", only_sources=["indeed"]),
    ]

    assert state.user_profile is not None
    orch = Orchestrator(state.trace_store, state.bus)

    result = orch.run(
        OrchestratorInputs(profile=state.user_profile, sources=sources),
        task_id="hunt-bg",
    )

    output = result.output
    if output is None:
        return

    state.plan = output.plan

    # Populate jobs from discovery batch
    batch = output.results.get("discovery")
    if batch:
        state.jobs = [
            {
                "job_id": p.job_id,
                "title": p.title,
                "company": p.company,
                "location": p.location,
                "url": p.url,
                "source": p.source,
                "relevance_score": p.relevance_score,
                "ghost_score": p.ghost_score,
                "salary_min": p.salary_min,
                "salary_max": p.salary_max,
                "remote": p.remote,
                "status": "Saved",
            }
            for p in batch.postings
        ]

    # Populate applications from submission
    subs = output.results.get("submission", [])
    state.applications = [
        {
            "company": s.company,
            "title": s.title,
            "route": s.route,
            "requires_user_click": s.requires_user_click,
            "status": "Applied" if not s.requires_user_click else "Saved",
        }
        for s in subs
    ]

    # Enqueue tailored documents for approval
    docs = output.results.get("resume", [])
    for doc in docs:
        state.approval_queue.submit(
            job_id=doc.job_id,
            document_id=f"doc-{doc.job_id}",
            company=doc.company,
            title=doc.title,
        )
        state.bus.publish(
            "approval", "hunt-bg",
            f"Resume ready for {doc.company} — {doc.title}. Awaiting your approval.",
        )

    # Track plan step progress
    for step in output.plan.steps:
        state.hunt_progress[step.step_id] = step.status


async def _run_hunt_bg(state: DashboardState) -> None:
    """Async wrapper: runs synchronous orchestrator in a thread pool."""
    state.hunt_status = "running"
    state.bus.publish("orchestrator", "hunt-bg", "Hunt started — running all agents.")
    try:
        await asyncio.to_thread(_execute_hunt, state)
        state.hunt_status = "complete"
        state.bus.publish("orchestrator", "hunt-bg",
                          f"Hunt complete — {len(state.jobs)} jobs discovered, "
                          f"{len(state.applications)} applications queued.")
    except Exception as exc:
        state.hunt_status = "failed"
        state.hunt_error = str(exc)
        state.bus.publish("orchestrator", "hunt-bg", f"Hunt failed: {exc!r}")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(state: DashboardState):
    try:
        from contextlib import asynccontextmanager

        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "fastapi is not installed. Run `pip install fastapi uvicorn`."
        ) from exc

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        state.bus.set_loop(asyncio.get_event_loop())
        yield

    app = FastAPI(title="JobHunt Dashboard", version="0.2.0", lifespan=lifespan)

    # ------------------------------------------------------------------ static

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (Path(__file__).parent / "client.html").read_text()

    # ------------------------------------------------------------------ status

    @app.get("/api/status")
    def get_status() -> dict:
        return {
            "hunt_status": state.hunt_status,
            "hunt_error": state.hunt_error,
            "has_profile": state.user_profile is not None,
            "jobs_count": len(state.jobs),
            "approvals_pending": len(state.approval_queue.pending()),
        }

    # ---------------------------------------------------------------- onboarding

    @app.post("/api/onboarding/profile")
    def save_profile(body: dict) -> dict:
        required = {"name", "email", "target_roles", "locations"}
        missing = required - body.keys()
        if missing:
            raise HTTPException(status_code=422, detail=f"missing fields: {missing}")
        if not body["name"].strip():
            raise HTTPException(status_code=422, detail="name is required")
        if "@" not in body["email"]:
            raise HTTPException(status_code=422, detail="invalid email")
        if not body["target_roles"]:
            raise HTTPException(status_code=422, detail="at least one target role required")
        state.user_profile = build_user_profile(body)
        return {"ok": True, "user_id": state.user_profile.user_id}

    @app.post("/api/onboarding/resume")
    def parse_resume(body: dict) -> dict:
        text = body.get("text", "")
        if not text.strip():
            raise HTTPException(status_code=422, detail="resume text is required")
        result = parse_resume_text(text)
        # Merge extracted skills into existing profile if present
        if state.user_profile is not None:
            existing = set(state.user_profile.skills)
            merged = sorted(existing | set(result["skills"]))
            state.user_profile.skills = merged
        return result

    # ---------------------------------------------------------------- hunt control

    @app.post("/api/hunt/start")
    async def start_hunt() -> dict:
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        if state.hunt_status == "running":
            raise HTTPException(status_code=409, detail="hunt already running")
        # Reset any previous run
        state.jobs = []
        state.applications = []
        state.hunt_error = ""
        state.hunt_progress = {}
        asyncio.create_task(_run_hunt_bg(state))
        return {"ok": True, "hunt_status": "running"}

    # ------------------------------------------------------------------ plan / jobs

    @app.get("/api/plan")
    def get_plan() -> dict:
        if state.plan is None:
            return {"plan": None}
        return {"plan": _plan_to_dict(state.plan)}

    @app.get("/api/jobs")
    def get_jobs() -> dict:
        return {"jobs": state.jobs}

    @app.get("/api/applications")
    def get_apps() -> dict:
        return {"applications": state.applications}

    @app.get("/api/traces")
    def get_traces(agent: str | None = None, limit: int = 50) -> dict:
        traces = (
            state.trace_store.for_agent(agent)
            if agent
            else state.trace_store.all()
        )
        traces = list(reversed(traces))[:limit]
        return {"traces": [_trace_to_dict(t) for t in traces]}

    # ----------------------------------------------------------------- approvals

    @app.get("/api/approvals")
    def list_approvals(state_filter: str | None = None) -> dict:
        items = state.approval_queue.all()
        if state_filter:
            items = [r for r in items if r.state.value == state_filter]
        return {"approvals": [r.to_dict() for r in items]}

    @app.post("/api/approve/{identifier}")
    def approve(identifier: str, decision: str = "approve",
                reviewer: str = "", notes: str = "") -> dict:
        verb_to_state = {
            "approve": ApprovalState.APPROVED,
            "reject": ApprovalState.REJECTED,
            "edit": ApprovalState.EDIT_REQUESTED,
        }
        if decision not in verb_to_state:
            raise HTTPException(status_code=400, detail="bad decision")
        req = state.approval_queue.get(identifier)
        if req is None:
            pending = [r for r in state.approval_queue.by_job(identifier)
                       if r.state == ApprovalState.PENDING]
            req = pending[0] if pending else None
        if req is None:
            raise HTTPException(status_code=404, detail="unknown request")
        try:
            req = state.approval_queue.transition(
                req.request_id, verb_to_state[decision],
                reviewer=reviewer, notes=notes,
            )
        except InvalidTransition as e:
            raise HTTPException(status_code=409, detail=str(e))
        state.bus.publish("approval", req.job_id,
                          f"{req.company} → {decision} by {reviewer or 'anon'}")
        return {"ok": True, "request": req.to_dict()}

    # ----------------------------------------------------------------- WebSocket

    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async for payload in state.bus.subscribe():
                await ws.send_text(json.dumps(payload))
        except WebSocketDisconnect:
            return
        except Exception:  # pragma: no cover
            await ws.close()

    return app


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _plan_to_dict(plan: JobHuntPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "user_id": plan.user_id,
        "version": plan.version,
        "milestones": plan.milestones,
        "steps": [
            {
                "step_id": s.step_id,
                "agent": s.agent,
                "action": s.action,
                "status": s.status,
                "depends_on": s.depends_on,
            }
            for s in plan.steps
        ],
    }


def _trace_to_dict(trace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "agent": trace.agent,
        "task_id": trace.task_id,
        "thoughts": trace.thoughts,
        "self_critique": trace.self_critique,
        "decision": trace.decision,
        "confidence": trace.confidence,
        "tool_calls": [asdict(tc) for tc in trace.tool_calls],
        "created_at": trace.created_at,
    }
