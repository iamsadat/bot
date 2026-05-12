"""FastAPI dashboard.

Endpoints:

* ``GET  /``                  — single-page static client (mobile-first).
* ``GET  /api/plan``          — current JobHuntPlan.
* ``GET  /api/jobs``          — discovery batch postings (Kanban source).
* ``GET  /api/traces``        — reasoning traces (paginated by agent).
* ``GET  /api/applications``  — pipeline state.
* ``POST /api/approve/{job_id}`` — human one-click approval gate.
* ``WS   /ws/stream``         — live thought stream (server push).

The app is built around a single ``DashboardState`` container that the
Orchestrator updates as it runs. In production this would be backed by
Postgres + Redis pub/sub; the in-memory version here makes the demo
runnable with zero infrastructure.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jobhunt.approval import ApprovalQueue, ApprovalState, InvalidTransition
from jobhunt.models import JobHuntPlan
from jobhunt.trace import ThoughtBus, TraceStore


@dataclass
class DashboardState:
    trace_store: TraceStore
    bus: ThoughtBus
    plan: JobHuntPlan | None = None
    jobs: list[dict] = field(default_factory=list)
    applications: list[dict] = field(default_factory=list)
    approval_queue: ApprovalQueue = field(default_factory=ApprovalQueue)


def create_app(state: DashboardState):
    # Lazy import so the rest of the package works without fastapi.
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse
    except ImportError as exc:  # pragma: no cover - install-time guard
        raise RuntimeError(
            "fastapi is not installed. Install with `pip install fastapi uvicorn`."
        ) from exc

    app = FastAPI(title="JobHunt Dashboard", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        client = Path(__file__).parent / "client.html"
        return client.read_text()

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
        # Newest first; cap to ``limit``.
        traces = list(reversed(traces))[:limit]
        return {"traces": [_trace_to_dict(t) for t in traces]}

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
        # Accept either request_id (preferred) or job_id (UI back-compat).
        req = state.approval_queue.get(identifier)
        if req is None:
            pending = [r for r in state.approval_queue.by_job(identifier)
                       if r.state == ApprovalState.PENDING]
            if pending:
                req = pending[0]
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

    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async for payload in state.bus.subscribe():
                await ws.send_text(json.dumps(payload))
        except WebSocketDisconnect:
            return
        except Exception:  # pragma: no cover - defensive
            await ws.close()

    return app


# ---- serialization helpers (avoid leaking dataclass internals) -------------

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
