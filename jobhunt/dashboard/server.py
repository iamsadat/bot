"""FastAPI dashboard — onboarding + live pipeline + persistence.

Endpoints:

  GET  /                          single-page app (client.html)
  GET  /api/status                hunt lifecycle state
  POST /api/onboarding/profile    save user info + preferences
  POST /api/onboarding/resume     parse pasted resume text → extract skills
  POST /api/onboarding/ats        save ATS handles (greenhouse/lever/ashby)
  POST /api/hunt/start            kick off the background orchestrator
  POST /api/hunt/reset            clear everything and return to onboarding
  GET  /api/plan                  current execution plan (steps + statuses)
  GET  /api/jobs                  discovered job postings (Kanban source)
  POST /api/jobs/{job_id}/status  move a job between pipeline statuses
  GET  /api/applications          pipeline applications
  GET  /api/documents/{job_id}    fetch tailored resume + cover letter text
  GET  /api/documents/{job_id}/download
                                  download artifact (txt / pdf / docx)
  GET  /api/traces                reasoning traces (paginated)
  GET  /api/approvals             approval queue
  POST /api/approve/{id}          human one-click decision
  WS   /ws/stream                 live thought stream
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from jobhunt.approval import ApprovalQueue, ApprovalState, InvalidTransition
from jobhunt.dashboard.persistence import DashboardStore, restore_approval_queue
from jobhunt.models import JobHuntPlan, UserProfile
from jobhunt.onboarding import build_user_profile, parse_resume_text
from jobhunt.trace import ThoughtBus, TraceStore

# fastapi is an optional dependency for the rest of the package (importing
# this module should stay cheap even when fastapi isn't installed — see
# jobhunt/dashboard/__init__.py). We import it at module level (rather than
# inside create_app) so that, combined with `from __future__ import
# annotations`, FastAPI's own dependency-injection machinery can resolve
# string annotations like `Request`/`Response` against this module's
# globals when building route handlers.
try:
    from fastapi import (
        Depends, FastAPI, HTTPException, Request, Response as FastAPIResponse,
        WebSocket, WebSocketDisconnect,
    )
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    _FASTAPI_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:  # pragma: no cover
    _FASTAPI_IMPORT_ERROR = _exc
    Depends = FastAPI = HTTPException = Request = FastAPIResponse = None  # type: ignore
    WebSocket = WebSocketDisconnect = None  # type: ignore
    HTMLResponse = JSONResponse = Response = None  # type: ignore


_VALID_STATUSES = {"Saved", "Applied", "Assessment", "Interview", "Offer", "Closed"}

# Cookie name for per-workspace isolation, and the strict pattern a valid
# workspace id must match (secrets.token_hex(16) → 32 lowercase hex chars).
# Anything that doesn't match this is never trusted as a filesystem path
# component (path traversal defense-in-depth).
WORKSPACE_COOKIE = "jh_ws"
_SAFE_ID_RE = re.compile(r"^[a-f0-9]{32}$")



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
    documents: dict[str, dict] = field(default_factory=dict)  # job_id → doc dict
    user_profile: UserProfile | None = None
    # hunt lifecycle: idle | running | complete | failed
    hunt_status: str = "idle"
    hunt_error: str = ""
    hunt_progress: dict[str, str] = field(default_factory=dict)
    ats_config: dict = field(default_factory=dict)  # greenhouse_tokens / lever_slugs / ashby_slugs
    store: DashboardStore | None = None

    # ------------------------------------------------------------ persistence
    def persist(self) -> None:
        if self.store is None:
            return
        try:
            plan_dict = _plan_to_dict(self.plan) if self.plan else None
            self.store.save(
                profile=self.user_profile,
                jobs=self.jobs,
                applications=self.applications,
                approvals=self.approval_queue.all(),
                plan=plan_dict,
                documents=self.documents,
                hunt_status=self.hunt_status,
                hunt_error=self.hunt_error,
                ats_config=self.ats_config,
            )
        except Exception:
            pass  # never let persistence block the API

    def restore(self) -> None:
        if self.store is None:
            return
        snap = self.store.load()
        if snap is None:
            return
        self.user_profile = snap.get("profile")
        self.jobs = snap.get("jobs", [])
        self.applications = snap.get("applications", [])
        self.documents = snap.get("documents", {})
        self.hunt_status = snap.get("hunt_status", "idle")
        self.hunt_error = snap.get("hunt_error", "")
        self.ats_config = snap.get("ats_config", {})
        # Defensive: hunts that were mid-run when the server died → idle
        if self.hunt_status == "running":
            self.hunt_status = "idle"
        restore_approval_queue(self.approval_queue, snap.get("approvals", []))


# ---------------------------------------------------------------------------
# Workspace manager — production multi-tenant DashboardState cache
# ---------------------------------------------------------------------------

class WorkspaceManager:
    """LRU cache of per-workspace ``DashboardState`` objects.

    Each workspace gets its own SQLite-backed ``DashboardStore`` at
    ``base_dir / f"{ws_id}.db"``. ``ws_id`` MUST already be validated
    against ``_SAFE_ID_RE`` by the caller before reaching ``get`` — this
    class trusts its input is a safe path component.
    """

    def __init__(self, base_dir: Path | str, cap: int = 200) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cap = cap
        self._cache: OrderedDict[str, DashboardState] = OrderedDict()

    def get(self, ws_id: str) -> DashboardState:
        if not _SAFE_ID_RE.match(ws_id):
            raise ValueError(f"unsafe workspace id: {ws_id!r}")
        existing = self._cache.get(ws_id)
        if existing is not None:
            self._cache.move_to_end(ws_id)
            return existing

        new_state = DashboardState(
            trace_store=TraceStore(),
            bus=ThoughtBus(),
            store=DashboardStore(db_path=self.base_dir / f"{ws_id}.db"),
        )
        new_state.restore()
        self._cache[ws_id] = new_state
        self._cache.move_to_end(ws_id)

        # Evict least-recently-used entries once over capacity. No need to
        # re-persist on eviction — DashboardStore already writes synchronously
        # on every mutation, so the SQLite file is always up to date.
        while len(self._cache) > self.cap:
            self._cache.popitem(last=False)

        return new_state

    def __len__(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Background hunt runner
# ---------------------------------------------------------------------------

def _build_sources(ats_config: dict):
    """Construct JobSources from the user's ATS handles, fixture fallback."""
    from jobhunt.adapters import (
        AshbySource, FixtureSource, GreenhouseSource, LeverSource,
    )

    sources = []
    gh = [s.strip() for s in ats_config.get("greenhouse_tokens", []) if s.strip()]
    lv = [s.strip() for s in ats_config.get("lever_slugs", []) if s.strip()]
    ab = [s.strip() for s in ats_config.get("ashby_slugs", []) if s.strip()]
    if gh:
        sources.append(GreenhouseSource(board_tokens=gh))
    if lv:
        sources.append(LeverSource(companies=lv))
    if ab:
        sources.append(AshbySource(companies=ab))

    if not sources:
        # Offline fallback — uses fixture jobs so the demo always has data
        sources = [
            FixtureSource(name="greenhouse",
                          only_sources=["greenhouse", "ashby", "lever"]),
            FixtureSource(name="linkedin", only_sources=["linkedin"]),
            FixtureSource(name="indeed", only_sources=["indeed"]),
        ]
    return sources


def _execute_hunt(state: DashboardState) -> None:
    """Runs the full orchestrator pipeline synchronously (called in a thread)."""
    from jobhunt.agents.orchestrator import Orchestrator, OrchestratorInputs
    from jobhunt.llm.callbacks import resume_callback
    from jobhunt.llm.factory import build_llm_client_from_env

    sources = _build_sources(state.ats_config)

    assert state.user_profile is not None
    llm_client = build_llm_client_from_env()
    llm_cb = resume_callback(llm_client) if llm_client is not None else None
    orch = Orchestrator(state.trace_store, state.bus, llm=llm_cb)

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
                "posted_at": p.posted_at,
            }
            for p in batch.postings
        ]

    # Populate submission packages
    subs = output.results.get("submission", [])
    state.applications = [
        {
            "job_id": s.job_id,
            "company": s.company,
            "title": "",  # filled below from doc
            "route": s.route,
            "requires_user_click": s.requires_user_click,
            "status": "Saved",
            "notes": s.notes,
        }
        for s in subs
    ]

    # Persist tailored documents for download + approval
    docs = output.results.get("resume", [])
    for doc in docs:
        state.documents[doc.job_id] = {
            "job_id": doc.job_id,
            "company": doc.company,
            "title": doc.title,
            "url": doc.url,
            "resume_text": doc.resume_text,
            "cover_letter_text": doc.cover_letter_text,
            "keyword_coverage": doc.keyword_coverage,
            "matched_keywords": doc.matched_keywords,
            "missing_keywords": doc.missing_keywords,
            "bullets": doc.bullets,
        }
        state.approval_queue.submit(
            job_id=doc.job_id,
            document_id=f"doc-{doc.job_id}",
            company=doc.company,
            title=doc.title,
        )
        state.bus.publish(
            "approval", "hunt-bg",
            f"Tailored resume ready: {doc.company} — {doc.title} "
            f"(coverage {doc.keyword_coverage:.0%}). Awaiting your approval.",
        )

    # Backfill application titles from documents
    for app in state.applications:
        if app["job_id"] in state.documents:
            app["title"] = state.documents[app["job_id"]]["title"]

    for step in output.plan.steps:
        state.hunt_progress[step.step_id] = step.status


async def _run_hunt_bg(state: DashboardState) -> None:
    state.hunt_status = "running"
    state.bus.publish("orchestrator", "hunt-bg", "Hunt started — running all agents.")
    state.persist()
    try:
        await asyncio.to_thread(_execute_hunt, state)
        state.hunt_status = "complete"
        state.bus.publish(
            "orchestrator", "hunt-bg",
            f"Hunt complete — {len(state.jobs)} jobs discovered, "
            f"{len(state.documents)} tailored resumes ready for review.",
        )
    except Exception as exc:
        state.hunt_status = "failed"
        state.hunt_error = str(exc)
        state.bus.publish("orchestrator", "hunt-bg", f"Hunt failed: {exc!r}")
    finally:
        state.persist()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    state: DashboardState | None = None,
    *,
    workspace_factory: Callable[[str], DashboardState] | None = None,
    access_code: str | None = None,
):
    if _FASTAPI_IMPORT_ERROR is not None:  # pragma: no cover
        raise RuntimeError(
            "fastapi is not installed. Run `pip install fastapi uvicorn`."
        ) from _FASTAPI_IMPORT_ERROR

    if workspace_factory is None and state is None:
        raise ValueError("create_app requires either `state` or `workspace_factory`")

    @asynccontextmanager
    async def lifespan(app):
        if state is not None:
            state.bus.set_loop(asyncio.get_event_loop())
        yield

    app = FastAPI(title="JobHunt Dashboard", version="0.3.0", lifespan=lifespan)

    # ------------------------------------------------------------- per-request state

    def get_state(request: Request, response: FastAPIResponse) -> DashboardState:
        if workspace_factory is None:
            return state  # legacy / test mode: single shared state, no cookie games
        ws_id = request.cookies.get(WORKSPACE_COOKIE)
        if not ws_id or not _SAFE_ID_RE.match(ws_id):
            ws_id = secrets.token_hex(16)
            response.set_cookie(
                WORKSPACE_COOKIE, ws_id, max_age=60 * 60 * 24 * 180,
                httponly=True, samesite="lax",
            )
        # NOTE: this dependency may run in a worker thread (FastAPI offloads
        # sync dependencies via run_in_threadpool), so we must NOT touch
        # asyncio.get_event_loop() here — there is no running loop in that
        # thread. ThoughtBus.set_loop() is wired up just-in-time instead,
        # from call sites that are guaranteed to run on the main loop (the
        # websocket handler, and start_hunt() right before backgrounding
        # the orchestrator).
        return workspace_factory(ws_id)

    # --------------------------------------------------------------- access gate

    if access_code:
        @app.middleware("http")
        async def _access_code_gate(request: Request, call_next):
            # Only the live-pipeline API is gated. The page shell and static
            # companions (/demo, /tracker, /app, /site, /walkthrough, /) must
            # always load so the gate is a soft door, not a wall around the
            # whole site.
            if request.url.path.startswith("/api/"):
                supplied = (
                    request.headers.get("X-Access-Code")
                    or request.query_params.get("code")
                )
                if supplied != access_code:
                    return JSONResponse(
                        status_code=401, content={"detail": "access code required"},
                    )
            return await call_next(request)

    # ------------------------------------------------------------------ static

    @app.get("/", response_class=HTMLResponse)
    def index(_state: DashboardState = Depends(get_state)) -> str:
        return (Path(__file__).parent / "client.html").read_text()

    # ---- static companions: cinematic demo, SSOT tracker, sample-data app ----
    _ROOT = Path(__file__).resolve().parent.parent  # jobhunt/

    def _serve(rel: str, media: str):
        path = _ROOT / rel
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"{rel} not found")
        data = path.read_bytes()
        return Response(content=data, media_type=media)

    @app.get("/demo")
    @app.get("/demo/demo.html")
    def demo_page():
        return _serve("demo/demo.html", "text/html; charset=utf-8")

    @app.get("/demo/jobhunt-demo.mp4")
    def demo_video():
        return _serve("demo/jobhunt-demo.mp4", "video/mp4")

    @app.get("/tracker")
    @app.get("/tracker/index.html")
    def tracker_page():
        return _serve("tracker/index.html", "text/html; charset=utf-8")

    @app.get("/tracker/tasks.json")
    def tracker_data():
        return _serve("tracker/tasks.json", "application/json")

    @app.get("/app")
    @app.get("/site/app.html")
    def sample_app():
        return _serve("site/app.html", "text/html; charset=utf-8")

    @app.get("/walkthrough")
    @app.get("/site/walkthrough.html")
    def walkthrough_page():
        return _serve("site/walkthrough.html", "text/html; charset=utf-8")

    # ------------------------------------------------------------------ status

    @app.get("/api/status")
    def get_status(state: DashboardState = Depends(get_state)) -> dict:
        applied = sum(1 for j in state.jobs if j.get("status") in
                      ("Applied", "Assessment", "Interview", "Offer"))
        return {
            "hunt_status": state.hunt_status,
            "hunt_error": state.hunt_error,
            "has_profile": state.user_profile is not None,
            "jobs_count": len(state.jobs),
            "applied_count": applied,
            "approvals_pending": len(state.approval_queue.pending()),
            "ats_configured": any(state.ats_config.get(k) for k in (
                "greenhouse_tokens", "lever_slugs", "ashby_slugs"
            )),
        }

    # ---------------------------------------------------------------- onboarding

    @app.post("/api/onboarding/profile")
    def save_profile(body: dict, state: DashboardState = Depends(get_state)) -> dict:
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
        state.persist()
        return {"ok": True, "user_id": state.user_profile.user_id}

    @app.post("/api/onboarding/resume")
    def parse_resume(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        text = body.get("text", "")
        if not text.strip():
            raise HTTPException(status_code=422, detail="resume text is required")
        result = parse_resume_text(text)
        if state.user_profile is not None:
            existing = set(state.user_profile.skills)
            merged = sorted(existing | set(result["skills"]))
            state.user_profile.skills = merged
            state.persist()
        return result

    @app.post("/api/onboarding/ats")
    def save_ats(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        def _parse_list(key: str) -> list[str]:
            v = body.get(key, "")
            if isinstance(v, list):
                return [s.strip() for s in v if str(s).strip()]
            return [s.strip() for s in str(v).split(",") if s.strip()]

        state.ats_config = {
            "greenhouse_tokens": _parse_list("greenhouse_tokens"),
            "lever_slugs": _parse_list("lever_slugs"),
            "ashby_slugs": _parse_list("ashby_slugs"),
        }
        state.persist()
        return {"ok": True, "ats_config": state.ats_config}

    # ---------------------------------------------------------------- hunt control

    @app.post("/api/hunt/start")
    async def start_hunt(state: DashboardState = Depends(get_state)) -> dict:
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        if state.hunt_status == "running":
            raise HTTPException(status_code=409, detail="hunt already running")
        state.jobs = []
        state.applications = []
        state.documents = {}
        state.hunt_error = ""
        state.hunt_progress = {}
        # We're on the main event loop here (this is an async route handler,
        # not a threaded dependency), so it's safe to wire up cross-thread
        # publishing now, just before the orchestrator starts running in a
        # worker thread via asyncio.to_thread.
        state.bus.set_loop(asyncio.get_event_loop())
        asyncio.create_task(_run_hunt_bg(state))
        return {"ok": True, "hunt_status": "running"}

    @app.post("/api/hunt/reset")
    def reset_hunt(state: DashboardState = Depends(get_state)) -> dict:
        if state.hunt_status == "running":
            raise HTTPException(status_code=409, detail="cannot reset while running")
        state.user_profile = None
        state.jobs = []
        state.applications = []
        state.documents = {}
        state.plan = None
        state.hunt_status = "idle"
        state.hunt_error = ""
        state.ats_config = {}
        state.approval_queue = ApprovalQueue()
        state.persist()
        return {"ok": True}

    # ------------------------------------------------------------------ plan / jobs

    @app.get("/api/plan")
    def get_plan(state: DashboardState = Depends(get_state)) -> dict:
        if state.plan is None:
            return {"plan": None}
        return {"plan": _plan_to_dict(state.plan)}

    @app.get("/api/jobs")
    def get_jobs(state: DashboardState = Depends(get_state)) -> dict:
        return {"jobs": state.jobs}

    @app.post("/api/jobs/{job_id}/status")
    def update_job_status(
        job_id: str, body: dict, state: DashboardState = Depends(get_state),
    ) -> dict:
        new_status = body.get("status", "")
        if new_status not in _VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of {sorted(_VALID_STATUSES)}",
            )
        for j in state.jobs:
            if j["job_id"] == job_id:
                old = j.get("status", "Saved")
                j["status"] = new_status
                state.bus.publish(
                    "tracking", job_id,
                    f"{j['company']} → {j['title']}: {old} → {new_status}",
                )
                state.persist()
                return {"ok": True, "job": j}
        raise HTTPException(status_code=404, detail="job not found")

    @app.get("/api/applications")
    def get_apps(state: DashboardState = Depends(get_state)) -> dict:
        return {"applications": state.applications}

    # ----------------------------------------------------------------- documents

    @app.get("/api/documents/{job_id}")
    def get_document(job_id: str, state: DashboardState = Depends(get_state)) -> dict:
        doc = state.documents.get(job_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        return {"document": doc}

    @app.get("/api/documents/{job_id}/download")
    def download_document(
        job_id: str, format: str = "txt", kind: str = "resume",
        state: DashboardState = Depends(get_state),
    ) -> Any:
        doc = state.documents.get(job_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        if kind not in ("resume", "cover"):
            raise HTTPException(status_code=400, detail="kind must be 'resume' or 'cover'")

        text = doc["resume_text"] if kind == "resume" else doc["cover_letter_text"]
        safe = f"{doc['company']}-{doc['title']}".replace(" ", "_").replace("/", "_")
        filename = f"{safe}-{kind}.{format}"

        if format == "txt":
            return Response(
                content=text.encode("utf-8"),
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if format == "html":
            html = _doc_to_simple_html(doc, kind)
            return Response(
                content=html.encode("utf-8"),
                media_type="text/html; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if format == "pdf":
            try:
                from weasyprint import HTML  # type: ignore
                html = _doc_to_simple_html(doc, kind)
                pdf = HTML(string=html).write_pdf()
                return Response(
                    content=pdf,
                    media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            except ImportError:
                raise HTTPException(
                    status_code=503,
                    detail="PDF unavailable — install weasyprint (pip install weasyprint)",
                )
        if format == "docx":
            try:
                from docx import Document  # type: ignore
                from io import BytesIO
                d = Document()
                for line in text.split("\n"):
                    d.add_paragraph(line)
                buf = BytesIO()
                d.save(buf)
                return Response(
                    content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            except ImportError:
                raise HTTPException(
                    status_code=503,
                    detail="DOCX unavailable — install python-docx (pip install python-docx)",
                )
        raise HTTPException(status_code=400, detail=f"unknown format: {format}")

    # ----------------------------------------------------------------- traces

    @app.get("/api/traces")
    def get_traces(
        agent: str | None = None, limit: int = 50,
        state: DashboardState = Depends(get_state),
    ) -> dict:
        traces = (
            state.trace_store.for_agent(agent)
            if agent
            else state.trace_store.all()
        )
        traces = list(reversed(traces))[:limit]
        return {"traces": [_trace_to_dict(t) for t in traces]}

    # ----------------------------------------------------------------- approvals

    @app.get("/api/approvals")
    def list_approvals(
        state_filter: str | None = None, state: DashboardState = Depends(get_state),
    ) -> dict:
        items = state.approval_queue.all()
        if state_filter:
            items = [r for r in items if r.state.value == state_filter]
        return {"approvals": [r.to_dict() for r in items]}

    @app.post("/api/approve/{identifier}")
    def approve(identifier: str, decision: str = "approve",
                reviewer: str = "", notes: str = "",
                state: DashboardState = Depends(get_state)) -> dict:
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
        # Auto-advance: when a resume is approved, mark the matching job as "Applied"
        if decision == "approve":
            for j in state.jobs:
                if j["job_id"] == req.job_id and j.get("status") == "Saved":
                    j["status"] = "Applied"
                    state.bus.publish(
                        "submission", req.job_id,
                        f"{j['company']} → {j['title']}: marked Applied "
                        f"(use 'Open job' to submit on the company site).",
                    )
                    break
        state.bus.publish("approval", req.job_id,
                          f"{req.company} → {decision} by {reviewer or 'anon'}")
        state.persist()
        return {"ok": True, "request": req.to_dict()}

    # ----------------------------------------------------------------- WebSocket

    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket) -> None:
        # A WebSocket can't set a *new* cookie on its handshake response, so
        # we can't mint a fresh workspace here the way `get_state` does for
        # HTTP routes. The client always hits `GET /` first via the SPA,
        # which sets the cookie — so by the time the socket connects, the
        # cookie should already be present. Absence is rare/abuse-only.
        if access_code:
            supplied = (
                ws.headers.get("X-Access-Code") or ws.query_params.get("code")
            )
            if supplied != access_code:
                await ws.close(code=1008)  # policy violation
                return

        if workspace_factory is None:
            ws_state = state
        else:
            ws_id = ws.cookies.get(WORKSPACE_COOKIE)
            if not ws_id or not _SAFE_ID_RE.match(ws_id):
                await ws.close(code=1008)  # policy violation
                return
            ws_state = workspace_factory(ws_id)
            ws_state.bus.set_loop(asyncio.get_event_loop())

        await ws.accept()
        try:
            async for payload in ws_state.bus.subscribe():
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


def _doc_to_simple_html(doc: dict, kind: str) -> str:
    text = doc["resume_text"] if kind == "resume" else doc["cover_letter_text"]
    body = "<br/>".join(
        line if line.strip() else "&nbsp;" for line in text.split("\n")
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{doc['company']} — {doc['title']}</title>
<style>
  body {{ font-family: -apple-system, "Helvetica Neue", Helvetica, sans-serif;
          max-width: 720px; margin: 32px auto; padding: 0 16px;
          color: #1a1a1a; line-height: 1.5; }}
  h1 {{ font-size: 1.4em; }}
</style></head><body>
  <h1>{doc['company']} — {doc['title']}</h1>
  <p>{body}</p>
</body></html>"""
