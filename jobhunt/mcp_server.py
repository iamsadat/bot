"""JobHunt MCP server — expose the pipeline as agent-callable tools.

Lets Claude (or any MCP client) drive a JobHunt instance: inspect the pipeline,
read a tailored résumé, search for jobs, and approve applications. The tool
*logic* (``JobHuntTools``) is pure and fully offline-testable; the thin MCP
wiring (``serve_stdio``) is behind an optional ``mcp`` import so the package and
its test suite need no new dependency.

Run:  ``python -m jobhunt mcp``  (needs ``pip install jobhunt[mcp]``).
"""

from __future__ import annotations

import os
from typing import Any

# Tool schemas advertised to MCP clients (kept in sync with JobHuntTools).
TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "jobhunt_status", "description": "Counts for the current pipeline "
     "(discovered / pending approval / applied).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "jobhunt_pipeline", "description": "Jobs grouped by status "
     "(Saved/Applied/Interview/…).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "jobhunt_list_jobs", "description": "List jobs, optionally filtered by status.",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "jobhunt_get_resume", "description": "Get the tailored résumé summary "
     "for a job_id (coverage, matched/missing keywords, plain text).",
     "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
    {"name": "jobhunt_search_jobs", "description": "Run discovery now for a role "
     "(+ optional location) against the connected sources; returns matches.",
     "input_schema": {"type": "object", "properties": {
         "role": {"type": "string"}, "location": {"type": "string"}},
         "required": ["role"]}},
    {"name": "jobhunt_approve", "description": "Approve a tailored résumé "
     "(marks the job Applied; real ATS submit still happens in the app).",
     "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
]


class JobHuntTools:
    """Pure tool implementations over a DashboardState. Unit-testable."""

    def __init__(self, state) -> None:
        self.state = state

    # ----- read --------------------------------------------------------------
    def status(self) -> dict:
        jobs = self.state.jobs
        applied = sum(1 for j in jobs if j.get("status") in
                      ("Applied", "Assessment", "Interview", "Offer"))
        return {
            "discovered": len(jobs),
            "applied": applied,
            "pending_approval": len(self.state.approval_queue.pending()),
            "documents": len(self.state.documents),
        }

    def pipeline(self) -> dict:
        cols: dict[str, list] = {}
        for j in self.state.jobs:
            cols.setdefault(j.get("status", "Saved"), []).append(
                {"job_id": j["job_id"], "title": j.get("title", ""),
                 "company": j.get("company", "")})
        return {"pipeline": cols}

    def list_jobs(self, status: str | None = None) -> dict:
        out = []
        for j in self.state.jobs:
            if status and j.get("status") != status:
                continue
            out.append({"job_id": j["job_id"], "title": j.get("title", ""),
                        "company": j.get("company", ""), "status": j.get("status", "Saved"),
                        "url": j.get("url", ""),
                        "relevance_score": j.get("relevance_score")})
        return {"jobs": out}

    def get_resume(self, job_id: str) -> dict:
        doc = self.state.documents.get(job_id)
        if doc is None:
            return {"error": f"no tailored résumé for {job_id}"}
        return {
            "job_id": job_id, "company": doc.get("company"), "title": doc.get("title"),
            "keyword_coverage": doc.get("keyword_coverage"),
            "matched_keywords": doc.get("matched_keywords", []),
            "missing_keywords": doc.get("missing_keywords", []),
            "resume_text": doc.get("resume_text", ""),
        }

    def search_jobs(self, role: str, location: str = "") -> dict:
        """Run discovery only (no persist) and return ranked matches."""
        from jobhunt.agents.discovery import DiscoveryAgent, DiscoveryInputs
        from jobhunt.dashboard.server import _build_sources

        profile = self.state.user_profile
        if profile is None:
            return {"error": "no profile yet — onboard first", "matches": []}
        sources = _build_sources(self.state.ats_config)
        agent = DiscoveryAgent(self.state.trace_store, self.state.bus)
        query = {"role": role, "location": location,
                 "remote_ok": getattr(profile, "remote_ok", True) if profile else True,
                 "exclude_companies": getattr(profile, "veto_companies", []) if profile else []}
        res = agent.run(
            DiscoveryInputs(profile=profile, queries=[query], sources=sources, plan_id="mcp"),
            task_id="mcp-search")
        batch = res.output
        postings = batch.postings if batch else []
        return {"matches": [
            {"job_id": p.job_id, "title": p.title, "company": p.company,
             "location": p.location, "url": p.url,
             "relevance_score": round(p.relevance_score, 3)}
            for p in postings[:25]
        ]}

    # ----- mutate ------------------------------------------------------------
    def approve(self, job_id: str) -> dict:
        from jobhunt.approval import ApprovalState, InvalidTransition

        pending = [r for r in self.state.approval_queue.by_job(job_id)
                   if r.state == ApprovalState.PENDING]
        if not pending:
            return {"ok": False, "error": f"no pending approval for {job_id}"}
        try:
            self.state.approval_queue.transition(
                pending[0].request_id, ApprovalState.APPROVED, reviewer="mcp")
        except InvalidTransition as exc:
            return {"ok": False, "error": str(exc)}
        for j in self.state.jobs:
            if j["job_id"] == job_id and j.get("status") == "Saved":
                j["status"] = "Applied"
        self.state.persist()
        return {"ok": True, "job_id": job_id, "status": "Applied"}

    # dispatch by tool name (used by the MCP wiring + tests)
    def call(self, name: str, args: dict) -> dict:
        table = {
            "jobhunt_status": lambda: self.status(),
            "jobhunt_pipeline": lambda: self.pipeline(),
            "jobhunt_list_jobs": lambda: self.list_jobs(args.get("status")),
            "jobhunt_get_resume": lambda: self.get_resume(args.get("job_id", "")),
            "jobhunt_search_jobs": lambda: self.search_jobs(
                args.get("role", ""), args.get("location", "")),
            "jobhunt_approve": lambda: self.approve(args.get("job_id", "")),
        }
        if name not in table:
            return {"error": f"unknown tool: {name}"}
        return table[name]()


def build_tools_from_env() -> JobHuntTools:
    """Load a DashboardState from the configured SQLite snapshot."""
    from jobhunt.dashboard.persistence import DashboardStore
    from jobhunt.dashboard.server import DashboardState
    from jobhunt.trace import ThoughtBus, TraceStore

    store = DashboardStore(os.environ.get("JOBHUNT_DB_PATH", "jobhunt.db"))
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(), store=store)
    state.restore()
    return JobHuntTools(state)


def serve_stdio() -> None:  # pragma: no cover - requires the optional mcp SDK
    """Run the MCP server over stdio. Needs ``pip install jobhunt[mcp]``."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP server needs the 'mcp' package — pip install jobhunt[mcp]"
        ) from exc

    tools = build_tools_from_env()
    server = FastMCP("jobhunt")

    def _register(spec: dict) -> None:
        name = spec["name"]

        def _fn(**kwargs: Any) -> dict:
            return tools.call(name, kwargs)

        _fn.__name__ = name
        _fn.__doc__ = spec["description"]
        server.add_tool(_fn, name=name, description=spec["description"])

    for spec in TOOL_SPECS:
        _register(spec)
    server.run()
