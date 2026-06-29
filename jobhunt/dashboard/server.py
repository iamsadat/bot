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
import os
import re
import secrets
import time
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
    from fastapi.staticfiles import StaticFiles
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
    applies_today: dict = field(default_factory=dict)  # date-iso → count (autonomy daily cap)
    store: DashboardStore | None = None
    notifier: Any = None  # optional jobhunt.notify.Notifier (not persisted)

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
                applies_today=self.applies_today,
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
        self.applies_today = snap.get("applies_today", {})
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
        AdzunaSource, AshbySource, FixtureSource, GreenhouseSource, LeverSource,
        PersonioSource, RecruiteeSource, USAJobsSource, WorkableSource,
    )

    def _slugs(key: str) -> list[str]:
        return [s.strip() for s in ats_config.get(key, []) if s.strip()]

    sources = []
    gh, lv, ab = _slugs("greenhouse_tokens"), _slugs("lever_slugs"), _slugs("ashby_slugs")
    rc, wk, pn = _slugs("recruitee_slugs"), _slugs("workable_slugs"), _slugs("personio_slugs")
    if gh:
        sources.append(GreenhouseSource(board_tokens=gh))
    if lv:
        sources.append(LeverSource(companies=lv))
    if ab:
        sources.append(AshbySource(companies=ab))
    if rc:
        sources.append(RecruiteeSource(companies=rc))
    if wk:
        sources.append(WorkableSource(accounts=wk))
    if pn:
        sources.append(PersonioSource(companies=pn))

    # Aggregators with native search use env-configured API keys (not per-user
    # ATS handles), so they augment whatever boards are connected.
    adz_id, adz_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if adz_id and adz_key:
        sources.append(AdzunaSource(
            app_id=adz_id, app_key=adz_key,
            country=os.environ.get("ADZUNA_COUNTRY", "us")))
    usa_email, usa_key = os.environ.get("USAJOBS_EMAIL"), os.environ.get("USAJOBS_API_KEY")
    if usa_email and usa_key:
        sources.append(USAJobsSource(email=usa_email, api_key=usa_key))

    if not sources:
        # Offline fallback — uses fixture jobs so the demo always has data
        sources = [
            FixtureSource(name="greenhouse",
                          only_sources=["greenhouse", "ashby", "lever"]),
            FixtureSource(name="linkedin", only_sources=["linkedin"]),
            FixtureSource(name="indeed", only_sources=["indeed"]),
        ]
    return sources


def _default_submitter_registry():
    """Real Greenhouse + Lever submitters over an urllib poster.

    Used by the live approve flow to actually POST applications. Tests inject
    a registry backed by ``FakePoster`` instead.
    """
    from jobhunt.submitters.base import UrllibPoster
    from jobhunt.submitters.greenhouse import (
        GreenhouseSubmitter, _default_question_fetcher,
    )
    from jobhunt.submitters.lever import LeverSubmitter
    from jobhunt.submitters.registry import SubmitterRegistry

    poster = UrllibPoster()
    return SubmitterRegistry([
        # Real fetcher so live submits answer the board's custom questions.
        GreenhouseSubmitter(poster, question_fetcher=_default_question_fetcher),
        LeverSubmitter(poster),
    ])


def _ats_connected(state: DashboardState) -> bool:
    """True when the user has configured real ATS handles (not fixtures).

    Auto-submission is gated on this: the offline fixtures use real-looking
    ``boards.greenhouse.io`` URLs, so without this gate, approving a fixture
    job would fire a real (garbage) POST to Greenhouse. Real submission only
    happens once the user has actually connected a board.
    """
    return any(
        state.ats_config.get(k)
        for k in ("greenhouse_tokens", "lever_slugs", "ashby_slugs")
    )


def _add_event(
    state: DashboardState, job_id: str, stage: str, detail: str,
    *, status: str = "done",
) -> None:
    """Append a lifecycle event to a job's per-application timeline.

    Stored inside the job dict (``job["events"]``) so it persists via the
    existing ``jobs_json`` snapshot with no schema change.
    """
    for j in state.jobs:
        if j["job_id"] == job_id:
            j.setdefault("events", []).append({
                "ts": time.time(), "stage": stage,
                "detail": detail, "status": status,
            })
            break


def _email_template(state: DashboardState, job_id: str, kind: str) -> tuple[str, str]:
    """Build a (subject, body) follow-up / thank-you for a job from templates."""
    doc = state.documents.get(job_id, {})
    job = next((j for j in state.jobs if j["job_id"] == job_id), {})
    company = doc.get("company") or job.get("company", "the team")
    title = doc.get("title") or job.get("title", "the role")
    name = state.user_profile.name if state.user_profile else ""
    if kind == "thank_you":
        subject = f"Thank you — {title}"
        body = (f"Hi,\n\nThank you for taking the time to discuss the {title} "
                f"role at {company}. I enjoyed the conversation and am very "
                f"excited about the opportunity to contribute.\n\nBest,\n{name}")
    else:  # follow_up
        subject = f"Following up — {title}"
        body = (f"Hi,\n\nI wanted to follow up on my application for the {title} "
                f"role at {company}. I remain very interested and would welcome "
                f"the chance to discuss how I can help the team.\n\nBest,\n{name}")
    return subject, body


def _notify(state: DashboardState, kind: str, title: str, body: str = "", url: str = "") -> None:
    """Fire a notification if a notifier is configured (best-effort)."""
    notifier = getattr(state, "notifier", None)
    if not notifier:
        return
    try:
        from jobhunt.notify import NotificationEvent
        notifier.notify(NotificationEvent(kind=kind, title=title, body=body, url=url))
    except Exception:
        pass


def _apply_parsed_resume(profile, result: dict) -> None:
    """Merge parsed résumé output into a profile: union skills, fill-empty
    structured sections (never clobber user edits), add new links."""
    profile.skills = sorted(set(profile.skills) | set(result.get("skills", [])))
    if not profile.experiences and result.get("experiences"):
        profile.experiences = result["experiences"]
    if not profile.education and result.get("education"):
        profile.education = result["education"]
    if not profile.projects and result.get("projects"):
        profile.projects = result["projects"]
    for k, v in (result.get("links") or {}).items():
        profile.links.setdefault(k, v)


def _auto_apply(state: DashboardState, registry, req, job, doc) -> dict | None:
    """Attempt real submission for a just-approved job. Returns a status dict.

    Real submission fires only when the user has connected ATS boards
    (``_ats_connected``) AND a submitter supports the job URL AND the job
    hasn't already been submitted — so offline fixtures never POST. Otherwise
    the job is left Applied for the user to finish on the company site.
    """
    if job is None:
        return None
    job_id = job["job_id"]
    company, title, url = job.get("company", ""), job.get("title", ""), job.get("url", "")

    has_route = (
        doc is not None
        and not job.get("submitted")
        and _ats_connected(state)
        and registry.for_url(url) is not None
    )
    if not has_route:
        _add_event(state, job_id, "Applied", "Marked Applied — finish on the company site")
        state.bus.publish(
            "submission", job_id,
            f"{company} → {title}: marked Applied "
            f"(open the posting to finish on the company site).",
        )
        return {"submitted": False, "manual": True}

    profile = state.user_profile
    resume_text = doc.get("resume_text", "")
    plan = {
        "url": url, "job_id": job_id,
        "applicant": {
            "name": profile.name if profile else "",
            "email": profile.email if profile else "",
            "phone": getattr(profile, "phone", "") if profile else "",
            "location": (profile.locations[0] if profile and profile.locations else ""),
        },
        "resume_text": resume_text,
        "cover_letter_text": doc.get("cover_letter_text", ""),
        # Standard answers to the board's custom screening questions.
        "answers": getattr(profile, "application_answers", {}) if profile else {},
    }
    # Render a real PDF so the upload is a valid file, not text mislabeled as PDF.
    # Prefer the structured single-column layout when a draft is available.
    try:
        draft_dict = doc.get("draft")
        if draft_dict:
            from jobhunt.resume_renderer import draft_to_pdf
            from jobhunt.resume_template import ResumeDraft
            plan["resume_pdf"] = draft_to_pdf(ResumeDraft.from_dict(draft_dict))
        else:
            from jobhunt.resume_renderer import text_to_pdf
            lines = resume_text.split("\n")
            heading = lines[0].strip() if lines and lines[0].strip() else company
            plan["resume_pdf"] = text_to_pdf(heading, "\n".join(lines[1:]))
    except Exception:
        pass  # fpdf2 missing → submitters fall back to encoding the plain text
    try:
        result = registry.submit(plan)
        ok = bool(result and result.ok)
        sub_id = result.submission_id if result else ""
        detail = result.detail if result else "no submitter"
    except Exception as exc:  # a network/parse error must not 500 the approve
        ok, sub_id, detail = False, "", f"error: {exc}"

    if ok:
        job["submitted"] = True
        job["submission_id"] = sub_id
        try:
            state.approval_queue.transition(req.request_id, ApprovalState.SUBMITTED)
        except InvalidTransition:
            pass
        suffix = f" (id {sub_id})" if sub_id else ""
        _add_event(state, job_id, "Submitted", f"Auto-submitted to {company}{suffix}")
        state.bus.publish(
            "submission", job_id, f"{company} → {title}: auto-submitted{suffix}.",
        )
        return {"submitted": True, "submission_id": sub_id}

    _add_event(
        state, job_id, "Submit failed",
        f"Submission to {company} failed: {detail}", status="failed",
    )
    state.bus.publish(
        "submission", job_id, f"{company} → {title}: submission failed ({detail}).",
    )
    return {"submitted": False, "detail": detail}


def _job_dict_from_posting(p) -> dict:
    """Build a dashboard job dict (with fingerprint) from a JobPosting."""
    return {
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
        "fingerprint": p.fingerprint or p.compute_fingerprint(),
        "events": [{
            "ts": time.time(), "stage": "Discovered",
            "detail": f"Found on {p.source}"
            + (f" · {p.relevance_score:.0%} match" if p.relevance_score else ""),
            "status": "done",
        }],
    }


def _fingerprint(company: str, title: str, location: str) -> str:
    import hashlib
    key = "|".join([company.strip().lower(), title.strip().lower(),
                    location.strip().lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _merge_discovered(state: DashboardState, postings) -> int:
    """Append new postings to ``state.jobs`` by fingerprint. Never clears.

    Returns the number of genuinely new jobs added. This is what makes the
    continuous engine *accumulate* discoveries instead of replacing them on
    every run (the one-shot hunt still resets via ``start_hunt``).
    """
    seen = {
        j.get("fingerprint") or _fingerprint(
            j.get("company", ""), j.get("title", ""), j.get("location", ""))
        for j in state.jobs
    }
    added = 0
    for p in postings:
        fp = p.fingerprint or p.compute_fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        state.jobs.append(_job_dict_from_posting(p))
        added += 1
    return added


def _persist_tailored_docs(state: DashboardState, docs, *, task: str = "hunt-bg") -> int:
    """Store tailored docs + open approvals for any not already present.

    Idempotent by job_id, so it's safe to call repeatedly in continuous mode
    without re-queuing the same resume. Returns the count newly tailored.
    """
    new = 0
    for doc in docs:
        if doc.job_id in state.documents:
            continue
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
            "draft": doc.draft,
        }
        state.approval_queue.submit(
            job_id=doc.job_id,
            document_id=f"doc-{doc.job_id}",
            company=doc.company,
            title=doc.title,
        )
        state.bus.publish(
            "approval", task,
            f"Tailored resume ready: {doc.company} — {doc.title} "
            f"(coverage {doc.keyword_coverage:.0%}). Awaiting your approval.",
        )
        _add_event(
            state, doc.job_id, "Tailored",
            f"Resume tailored · {doc.keyword_coverage:.0%} ATS coverage · awaiting approval",
            status="running",
        )
        new += 1
    return new


def _execute_hunt(state: DashboardState, registry=None) -> None:
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

    # Populate jobs from discovery batch (one-shot hunt replaces wholesale).
    batch = output.results.get("discovery")
    if batch:
        state.jobs = [_job_dict_from_posting(p) for p in batch.postings]

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
    _persist_tailored_docs(state, docs)

    # Backfill application titles from documents
    for app in state.applications:
        if app["job_id"] in state.documents:
            app["title"] = state.documents[app["job_id"]]["title"]

    for step in output.plan.steps:
        state.hunt_progress[step.step_id] = step.status

    if registry is not None:
        _maybe_auto_apply_batch(state, registry)


# --------------------------------------------------------------------------- #
# Continuous discovery + autonomous auto-apply
# --------------------------------------------------------------------------- #

def _autonomy_enabled(state: DashboardState) -> bool:
    p = state.user_profile
    if p is not None and p.auto_apply:
        return True
    return os.environ.get("JOBHUNT_AUTO_APPLY", "").lower() in ("1", "true", "yes")


def _daily_cap(state: DashboardState) -> int:
    p = state.user_profile
    cap = p.daily_apply_cap if p and p.daily_apply_cap else 0
    env = os.environ.get("JOBHUNT_DAILY_APPLY_CAP")
    if env:
        try:
            cap = int(env)
        except ValueError:
            pass
    return cap if cap > 0 else 20  # sensible safety default when enabled


def _applied_today(state: DashboardState) -> int:
    from datetime import date
    return int(state.applies_today.get(date.today().isoformat(), 0))


def _maybe_auto_apply_batch(state: DashboardState, registry) -> int:
    """Autonomously approve + submit pending resumes, capped per day.

    Fires only when autonomy is enabled AND a real ATS is connected AND a
    submitter supports the job URL AND the job clears the relevance floor — so
    fixtures and disconnected boards are never auto-submitted. Returns the count
    actually submitted.
    """
    from datetime import date

    if not _autonomy_enabled(state) or not _ats_connected(state):
        return 0
    cap = _daily_cap(state)
    today = date.today().isoformat()
    used = int(state.applies_today.get(today, 0))
    remaining = cap - used
    if remaining <= 0:
        return 0
    floor = state.user_profile.relevance_floor if state.user_profile else 0.0

    applied = 0
    for req in list(state.approval_queue.pending()):
        if applied >= remaining:
            break
        job = next((j for j in state.jobs if j["job_id"] == req.job_id), None)
        doc = state.documents.get(req.job_id)
        if job is None or doc is None or job.get("submitted"):
            continue
        if registry.for_url(job.get("url", "")) is None:
            continue  # not a real-submittable board → leave for manual review
        if float(job.get("relevance_score") or 0.0) < floor:
            continue
        try:
            state.approval_queue.transition(
                req.request_id, ApprovalState.APPROVED, reviewer="auto")
        except InvalidTransition:
            continue
        if job.get("status") == "Saved":
            job["status"] = "Applied"
        _add_event(state, req.job_id, "Approved", "Auto-approved (autonomous mode)")
        res = _auto_apply(state, registry, req, job, doc)
        if res and res.get("submitted"):
            applied += 1

    if applied:
        state.applies_today[today] = used + applied
        state.bus.publish(
            "autonomy", "auto",
            f"Autonomously applied to {applied} role(s) "
            f"({used + applied}/{cap} today).",
        )
        _notify(state, "applied", f"Auto-applied to {applied} role(s)",
                f"{used + applied}/{cap} applications today.")
        state.persist()
    return applied


def _discover_once(state: DashboardState, registry) -> dict:
    """One continuous-mode cycle: discover → merge → tailor new → auto-apply.

    Unlike ``_execute_hunt`` this MERGES into existing state (never clears) and
    only tailors jobs it hasn't seen, so the dashboard accumulates over time.
    """
    from jobhunt.agents.orchestrator import Orchestrator, OrchestratorInputs
    from jobhunt.llm.callbacks import resume_callback
    from jobhunt.llm.factory import build_llm_client_from_env

    if state.user_profile is None:
        return {"added": 0, "tailored": 0, "applied": 0}

    sources = _build_sources(state.ats_config)
    llm_client = build_llm_client_from_env()
    llm_cb = resume_callback(llm_client) if llm_client is not None else None
    orch = Orchestrator(state.trace_store, state.bus, llm=llm_cb)
    result = orch.run(
        OrchestratorInputs(profile=state.user_profile, sources=sources),
        task_id="discover-bg",
    )
    output = result.output
    if output is None:
        return {"added": 0, "tailored": 0, "applied": 0}

    state.plan = output.plan
    batch = output.results.get("discovery")
    added = _merge_discovered(state, batch.postings) if batch else 0
    tailored = _persist_tailored_docs(state, output.results.get("resume", []),
                                      task="discover-bg")
    if added:
        _notify(state, "discovered", f"{added} new job match(es)",
                f"{tailored} tailored and ready to review.")
    applied = _maybe_auto_apply_batch(state, registry)
    state.persist()
    return {"added": added, "tailored": tailored, "applied": applied}


async def _run_hunt_bg(state: DashboardState, registry=None) -> None:
    state.hunt_status = "running"
    state.bus.publish("orchestrator", "hunt-bg", "Hunt started — running all agents.")
    state.persist()
    try:
        await asyncio.to_thread(_execute_hunt, state, registry)
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
    dev_nav: bool = False,
    submitter_registry=None,
):
    if _FASTAPI_IMPORT_ERROR is not None:  # pragma: no cover
        raise RuntimeError(
            "fastapi is not installed. Run `pip install fastapi uvicorn`."
        ) from _FASTAPI_IMPORT_ERROR

    if workspace_factory is None and state is None:
        raise ValueError("create_app requires either `state` or `workspace_factory`")

    # Recruiter-email auto-status: an IMAP source built from env (None if
    # unconfigured). Single-tenant `serve` mode polls it in the background;
    # the manual sync endpoint works in any mode.
    from jobhunt.dashboard.inbox_sync import build_inbox_from_env, sync_inbox
    inbox_source = build_inbox_from_env()
    _inbox_since = {"ts": 0.0}

    # Outbound notifications (Slack/Discord/Telegram/webhook/email). Attached to
    # the single-tenant state so the continuous/autonomy helpers can fire them.
    from jobhunt.notify import build_notifier_from_env
    notifier = build_notifier_from_env()
    if state is not None and notifier is not None:
        state.notifier = notifier

    # Google: Gmail sender (follow-ups/thank-yous) + Calendar (interview holds).
    # Built from env OAuth creds; None when unconfigured. (Gmail inbox is wired
    # via build_inbox_from_env above, which prefers Gmail over IMAP.)
    from jobhunt.integrations.google_factory import (
        build_calendar_from_env, build_gmail_sender_from_env,
    )
    gmail_sender = build_gmail_sender_from_env()
    calendar = build_calendar_from_env()

    # Recruiter-contact enrichment (Hunter/Apollo/PDL) for outreach.
    from jobhunt.integrations.enrichment import build_contact_finder_from_env
    contact_finder = build_contact_finder_from_env()

    # Submitter registry for the auto-apply flow. Defaults to real Greenhouse +
    # Lever submitters; tests inject one backed by FakePoster. Defined before
    # the lifespan so the continuous-discovery loop can reach it.
    registry = (
        submitter_registry if submitter_registry is not None
        else _default_submitter_registry()
    )

    @asynccontextmanager
    async def lifespan(app):
        if state is not None:
            state.bus.set_loop(asyncio.get_event_loop())
        poll_task = None
        disc_task = None
        if state is not None and inbox_source is not None:
            interval = int(os.environ.get("JOBHUNT_IMAP_POLL_SECONDS", "300"))

            async def _poll_loop():
                while True:
                    await asyncio.sleep(interval)
                    res = await asyncio.to_thread(
                        sync_inbox, state, inbox_source, since=_inbox_since["ts"],
                    )
                    _inbox_since["ts"] = time.time()
                    if res.get("updates"):
                        state.bus.publish(
                            "inbox", "poll",
                            f"Inbox sync: {res['updates']} application(s) updated.",
                        )
                        _notify(state, "interview",
                                f"{res['updates']} application update(s) from email",
                                "Check your pipeline for new interview/assessment status.")

            poll_task = asyncio.create_task(_poll_loop())

        # Continuous discovery: single-tenant serve mode only, off unless a
        # non-zero interval is set (so tests + the one-shot hunt are unchanged).
        if state is not None:
            disc_interval = int(os.environ.get("JOBHUNT_DISCOVERY_POLL_SECONDS", "0"))
            if disc_interval > 0:
                async def _disc_loop():
                    while True:
                        await asyncio.sleep(disc_interval)
                        try:
                            res = await asyncio.to_thread(_discover_once, state, registry)
                            if res.get("added") or res.get("applied"):
                                state.bus.publish(
                                    "discovery", "poll",
                                    f"Continuous sweep: +{res.get('added', 0)} jobs, "
                                    f"{res.get('tailored', 0)} tailored, "
                                    f"{res.get('applied', 0)} auto-applied.",
                                )
                        except Exception as exc:  # never let the loop die
                            state.bus.publish(
                                "discovery", "poll",
                                f"Continuous discovery error: {exc!r}",
                            )

                disc_task = asyncio.create_task(_disc_loop())
        try:
            yield
        finally:
            for t in (poll_task, disc_task):
                if t is not None:
                    t.cancel()

    app = FastAPI(title="JobHunt Dashboard", version="0.3.0", lifespan=lifespan)

    # Local-dev CORS: the Next.js dev server (next dev on :3000) is a different
    # origin. In production the frontend is static-exported and served from this
    # same app, so no CORS is needed there. Comma-separated origins, opt-in.
    _cors = [o.strip() for o in os.environ.get("JOBHUNT_CORS_ORIGINS", "").split(",") if o.strip()]
    if _cors:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware, allow_origins=_cors, allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )

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

    # Modern Next.js frontend (static export → frontend/out), built via
    # `cd frontend && npm ci && npm run build` (the Dockerfile does this). When
    # present it is served at the site ROOT and the legacy single-file SPA moves
    # to /legacy. When ABSENT (offline test suite / CI — no Node build), `/`
    # keeps serving the legacy SPA so existing tests are unchanged.
    _frontend_dir = Path(
        os.environ.get("JOBHUNT_FRONTEND_DIR")
        or (Path(__file__).resolve().parents[2] / "frontend" / "out")
    )
    _frontend_built = _frontend_dir.is_dir() and (_frontend_dir / "index.html").exists()

    @app.get("/", response_class=HTMLResponse)
    def index(_state: DashboardState = Depends(get_state)) -> str:
        # Keep Depends(get_state) so the workspace cookie is still minted here.
        if _frontend_built:
            return (_frontend_dir / "index.html").read_text(encoding="utf-8")
        return (Path(__file__).parent / "client.html").read_text(encoding="utf-8")

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_index(_state: DashboardState = Depends(get_state)) -> str:
        return (Path(__file__).parent / "client.html").read_text(encoding="utf-8")

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
        from jobhunt.llm.factory import describe_llm_from_env
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
            # UI flags: whether to show dev-only nav (tracker/demo), and which
            # LLM (if any) will tone-polish resumes for this deployment.
            "dev_nav": dev_nav,
            "llm": describe_llm_from_env(),
            "inbox_connected": inbox_source is not None,
            "auto_apply": (state.user_profile.auto_apply
                           if state.user_profile else False),
            "applied_today": _applied_today(state),
            "continuous": int(os.environ.get("JOBHUNT_DISCOVERY_POLL_SECONDS", "0")) > 0,
            "notify_channels": [s.name for s in notifier.sinks] if notifier else [],
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
            _apply_parsed_resume(state.user_profile, result)
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
            "recruitee_slugs": _parse_list("recruitee_slugs"),
            "workable_slugs": _parse_list("workable_slugs"),
            "personio_slugs": _parse_list("personio_slugs"),
        }
        state.persist()
        return {"ok": True, "ats_config": state.ats_config}

    # ------------------------------------------------------------------ profile

    @app.get("/api/profile")
    def get_profile(state: DashboardState = Depends(get_state)) -> dict:
        if state.user_profile is None:
            return {"profile": None}
        return {"profile": state.user_profile.to_dict(), "ats_config": state.ats_config}

    @app.put("/api/profile")
    def update_profile(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        p = state.user_profile

        def _as_list(key: str, current: list[str]) -> list[str]:
            if key not in body:
                return current
            v = body[key]
            if isinstance(v, list):
                return [str(s).strip() for s in v if str(s).strip()]
            return [s.strip() for s in str(v).split(",") if s.strip()]

        if "name" in body:
            name = str(body["name"]).strip()
            if not name:
                raise HTTPException(status_code=422, detail="name cannot be empty")
            p.name = name
        if "email" in body:
            email = str(body["email"]).strip()
            if "@" not in email:
                raise HTTPException(status_code=422, detail="invalid email")
            p.email = email.lower()
        if "phone" in body:
            p.phone = str(body["phone"]).strip()
        p.target_roles = _as_list("target_roles", p.target_roles)
        p.locations = _as_list("locations", p.locations)
        p.skills = [s.lower() for s in _as_list("skills", p.skills)]
        p.veto_companies = _as_list("veto_companies", p.veto_companies)
        p.culture_keywords = _as_list("culture_keywords", p.culture_keywords)
        if "min_salary" in body:
            p.min_salary = int(body["min_salary"]) if body["min_salary"] else None
        if "remote_ok" in body:
            p.remote_ok = bool(body["remote_ok"])
        if "weekly_target" in body:
            p.weekly_target = int(body["weekly_target"] or 10)
        if "application_answers" in body and isinstance(body["application_answers"], dict):
            p.application_answers = {
                k: v for k, v in body["application_answers"].items()
                if str(v).strip() != ""
            }
        if "links" in body and isinstance(body["links"], dict):
            p.links = {k: str(v).strip() for k, v in body["links"].items()
                       if str(v).strip()}
        if "auto_apply" in body:
            p.auto_apply = bool(body["auto_apply"])
        if "daily_apply_cap" in body:
            p.daily_apply_cap = max(0, int(body["daily_apply_cap"] or 0))
        if "relevance_floor" in body:
            p.relevance_floor = max(0.0, min(1.0, float(body["relevance_floor"] or 0.0)))

        state.persist()
        state.bus.publish("profile", p.user_id, "Profile updated.")
        return {"ok": True, "profile": p.to_dict()}

    @app.put("/api/profile/structured")
    def update_structured(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Replace the structured resume sections from the builder UI.

        Accepts ``experiences``/``education``/``projects`` arrays and a
        ``links`` map. Each is replaced wholesale (the builder owns them).
        """
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        p = state.user_profile

        def _list_of_dicts(key: str, current: list) -> list:
            v = body.get(key)
            if v is None:
                return current
            if not isinstance(v, list):
                raise HTTPException(status_code=422, detail=f"{key} must be a list")
            return [dict(item) for item in v if isinstance(item, dict)]

        p.experiences = _list_of_dicts("experiences", p.experiences)
        p.education = _list_of_dicts("education", p.education)
        p.projects = _list_of_dicts("projects", p.projects)
        if "links" in body and isinstance(body["links"], dict):
            p.links = {k: str(v).strip() for k, v in body["links"].items()
                       if str(v).strip()}
        state.persist()
        state.bus.publish("profile", p.user_id, "Resume sections updated.")
        return {"ok": True, "profile": p.to_dict()}

    @app.post("/api/profile/parse-resume-file")
    def parse_resume_file(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Parse an uploaded résumé file (base64 JSON — no multipart dep).

        Body: ``{"filename": "cv.docx", "content_base64": "..."}``. Extracts
        text (.txt/.docx/.pdf) then runs the same structured parse + fill-empty
        merge as the paste endpoint.
        """
        import base64

        from jobhunt.onboarding import ResumeFileError, extract_resume_text

        b64 = body.get("content_base64", "")
        if not b64:
            raise HTTPException(status_code=422, detail="content_base64 is required")
        try:
            data = base64.b64decode(b64)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid base64: {exc}")
        try:
            text = extract_resume_text(body.get("filename", ""), data)
        except ResumeFileError as exc:
            raise HTTPException(status_code=415, detail=str(exc))
        result = parse_resume_text(text)
        if state.user_profile is not None:
            _apply_parsed_resume(state.user_profile, result)
            state.persist()
        return result

    @app.post("/api/profile/import-github")
    def import_github(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Import a GitHub user's public repos as Project entries."""
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        username = str(body.get("username", "")).strip().lstrip("@")
        if not username:
            raise HTTPException(status_code=422, detail="username is required")
        from jobhunt.integrations import GitHubClient, GitHubError, repos_to_projects
        try:
            repos = GitHubClient().fetch_repos(username)
        except GitHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        projects = repos_to_projects(repos)
        p = state.user_profile
        seen = {str(pr.get("name", "")).lower() for pr in p.projects}
        added = [pr for pr in projects if pr["name"].lower() not in seen]
        p.projects = list(p.projects) + added
        p.links.setdefault("github", f"https://github.com/{username}")
        state.persist()
        state.bus.publish("profile", p.user_id,
                          f"Imported {len(added)} project(s) from GitHub.")
        return {"ok": True, "added": len(added), "projects": p.projects}

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
        asyncio.create_task(_run_hunt_bg(state, registry))
        return {"ok": True, "hunt_status": "running"}

    @app.post("/api/discover")
    async def discover_now(state: DashboardState = Depends(get_state)) -> dict:
        """Run a single continuous-style sweep now (merge, don't clear)."""
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        state.bus.set_loop(asyncio.get_event_loop())
        res = await asyncio.to_thread(_discover_once, state, registry)
        return {"ok": True, **res}

    @app.get("/api/autonomy")
    def get_autonomy(state: DashboardState = Depends(get_state)) -> dict:
        p = state.user_profile
        return {
            "auto_apply": bool(p.auto_apply) if p else False,
            "daily_apply_cap": int(p.daily_apply_cap) if p else 0,
            "relevance_floor": float(p.relevance_floor) if p else 0.0,
            "ats_connected": _ats_connected(state),
            "applied_today": _applied_today(state),
            "effective_cap": _daily_cap(state) if _autonomy_enabled(state) else 0,
            "continuous": int(os.environ.get("JOBHUNT_DISCOVERY_POLL_SECONDS", "0")) > 0,
        }

    @app.post("/api/autonomy")
    def set_autonomy(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        p = state.user_profile
        if "auto_apply" in body:
            p.auto_apply = bool(body["auto_apply"])
        if "daily_apply_cap" in body:
            p.daily_apply_cap = max(0, int(body["daily_apply_cap"] or 0))
        if "relevance_floor" in body:
            p.relevance_floor = max(0.0, min(1.0, float(body["relevance_floor"] or 0.0)))
        state.persist()
        state.bus.publish(
            "autonomy", p.user_id,
            f"Autonomy {'on' if p.auto_apply else 'off'} "
            f"(cap {p.daily_apply_cap or 'default'}, floor {p.relevance_floor:.0%}).",
        )
        return {"ok": True, "auto_apply": p.auto_apply,
                "daily_apply_cap": p.daily_apply_cap,
                "relevance_floor": p.relevance_floor}

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
                _add_event(state, job_id, new_status, f"Moved {old} → {new_status}")
                state.persist()
                return {"ok": True, "job": j}
        raise HTTPException(status_code=404, detail="job not found")

    @app.get("/api/jobs/{job_id}/timeline")
    def get_timeline(job_id: str, state: DashboardState = Depends(get_state)) -> dict:
        for j in state.jobs:
            if j["job_id"] == job_id:
                return {"timeline": j.get("events", [])}
        raise HTTPException(status_code=404, detail="job not found")

    @app.post("/api/jobs/{job_id}/notes")
    def set_job_notes(
        job_id: str, body: dict, state: DashboardState = Depends(get_state),
    ) -> dict:
        """Per-application notes + next action (powers the tracker view)."""
        for j in state.jobs:
            if j["job_id"] == job_id:
                if "notes" in body:
                    j["notes"] = str(body["notes"])
                if "next_action" in body:
                    j["next_action"] = str(body["next_action"]).strip()
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

        from jobhunt.resume_renderer import (
            RendererUnavailable, draft_to_docx, draft_to_pdf, draft_to_styled_html,
            text_to_docx, text_to_pdf, text_to_styled_html,
        )
        from jobhunt.resume_template import ResumeDraft

        text = doc["resume_text"] if kind == "resume" else doc["cover_letter_text"]
        safe = f"{doc['company']}-{doc['title']}".replace(" ", "_").replace("/", "_")
        filename = f"{safe}-{kind}.{format}"

        # Prefer the structured single-column layout when a draft is present and
        # we're rendering the resume (cover letters stay plain-text bodies).
        draft = None
        if kind == "resume" and doc.get("draft"):
            try:
                draft = ResumeDraft.from_dict(doc["draft"])
            except Exception:
                draft = None

        # Legacy heading/body split for the text-based renderers (fallback path).
        if kind == "resume":
            lines = text.split("\n")
            heading = lines[0].strip() if lines and lines[0].strip() else doc["title"]
            body = "\n".join(lines[1:])
        else:
            heading = f"Cover letter — {doc['company']}"
            body = text

        def _attach(content: bytes, media: str) -> Any:
            return Response(
                content=content, media_type=media,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        if format == "txt":
            return _attach(text.encode("utf-8"), "text/plain; charset=utf-8")
        if format == "html":
            if draft is not None:
                html = draft_to_styled_html(draft)
            else:
                html = text_to_styled_html(
                    heading, body, tab_title=f"{doc['company']} — {doc['title']}",
                )
            return _attach(html.encode("utf-8"), "text/html; charset=utf-8")
        if format == "pdf":
            try:
                pdf = draft_to_pdf(draft) if draft is not None else text_to_pdf(heading, body)
                return _attach(pdf, "application/pdf")
            except RendererUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e))
        if format == "docx":
            try:
                docx = draft_to_docx(draft) if draft is not None else text_to_docx(heading, body)
                return _attach(
                    docx,
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document",
                )
            except RendererUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e))
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

    # ----------------------------------------------------------------- activity

    @app.get("/api/activity")
    def get_activity(
        limit: int = 100, state: DashboardState = Depends(get_state),
    ) -> dict:
        # The ThoughtBus keeps a rolling history of every published event,
        # carrying structured reasoning fields (phase/considered/rejected/
        # confidence/decision) when emitted via an agent's emit(). Surface it
        # newest-first, plus a grouping by (agent, task_id) for the reasoning UI.
        events = list(reversed(state.bus.history()))[:limit]
        grouped: dict[str, list[dict]] = {}
        for ev in events:
            key = f"{ev.get('agent', '')}:{ev.get('task_id', '')}"
            grouped.setdefault(key, []).append(ev)
        return {"activity": events, "grouped": grouped}

    # ----------------------------------------------------------------- inbox

    @app.get("/api/inbox/status")
    def inbox_status() -> dict:
        return {"connected": inbox_source is not None}

    # ----------------------------------------------------------------- notify

    @app.get("/api/notify/status")
    def notify_status() -> dict:
        channels = [s.name for s in notifier.sinks] if notifier else []
        return {"configured": bool(notifier), "channels": channels}

    @app.post("/api/notify/test")
    def notify_test() -> dict:
        if not notifier:
            raise HTTPException(
                status_code=400,
                detail="no channels configured — set JOBHUNT_SLACK_WEBHOOK / "
                       "JOBHUNT_DISCORD_WEBHOOK / JOBHUNT_TELEGRAM_* / "
                       "JOBHUNT_WEBHOOK_URLS",
            )
        from jobhunt.notify import NotificationEvent
        delivered = notifier.notify(NotificationEvent(
            kind="test", title="JobHunt test notification",
            body="If you can read this, your channel is wired up. 🎯"))
        return {"ok": True, "delivered": delivered,
                "channels": [s.name for s in notifier.sinks]}

    # ----------------------------------------------------------------- google

    @app.get("/api/google/status")
    def google_status() -> dict:
        return {"gmail_send": gmail_sender is not None,
                "calendar": calendar is not None,
                "gmail_inbox": getattr(inbox_source, "name", "") == "gmail"}

    @app.post("/api/email/send")
    def email_send(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Send a follow-up / thank-you via Gmail. Body: {to, subject?, body?,
        job_id?, kind?}. When job_id+kind given and body omitted, a template is
        filled from the job/profile."""
        if gmail_sender is None:
            raise HTTPException(status_code=400,
                                detail="Gmail not configured — set JOBHUNT_GOOGLE_*")
        to = str(body.get("to", "")).strip()
        if "@" not in to:
            raise HTTPException(status_code=422, detail="valid 'to' address required")
        subject = body.get("subject", "")
        text = body.get("body", "")
        if not text and body.get("job_id") and body.get("kind"):
            subject, text = _email_template(
                state, str(body["job_id"]), str(body["kind"]))
        if not text:
            raise HTTPException(status_code=422, detail="'body' or job_id+kind required")
        try:
            mid = gmail_sender.send(to, subject or "Following up", text)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"send failed: {exc}")
        return {"ok": True, "message_id": mid}

    @app.post("/api/calendar/hold")
    def calendar_hold(body: dict) -> dict:
        """Create a Google Calendar event. Body: {summary, start, end,
        description?, attendees?} with ISO-8601 start/end."""
        if calendar is None:
            raise HTTPException(status_code=400,
                                detail="Calendar not configured — set JOBHUNT_GOOGLE_*")
        if not (body.get("summary") and body.get("start") and body.get("end")):
            raise HTTPException(status_code=422, detail="summary/start/end required")
        try:
            ev = calendar.create_event(
                summary=body["summary"], start_iso=body["start"], end_iso=body["end"],
                description=body.get("description", ""),
                attendees=body.get("attendees") or None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"calendar error: {exc}")
        return {"ok": True, "event_id": ev.id, "html_link": ev.html_link}

    # ----------------------------------------------------------------- outreach

    @app.get("/api/outreach/status")
    def outreach_status() -> dict:
        return {"configured": contact_finder is not None,
                "provider": getattr(contact_finder, "name", "")}

    @app.post("/api/outreach/find")
    def outreach_find(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Find recruiter contacts for a job (or company/domain)."""
        if contact_finder is None:
            raise HTTPException(status_code=400,
                                detail="no contact provider — set JOBHUNT_HUNTER_API_KEY")
        from jobhunt.integrations.enrichment import domain_from_url
        job = next((j for j in state.jobs if j["job_id"] == body.get("job_id")), {})
        company = body.get("company") or job.get("company", "")
        domain = body.get("domain") or domain_from_url(job.get("url", ""))
        if not domain:
            raise HTTPException(status_code=422,
                                detail="could not infer company domain — pass 'domain'")
        try:
            contacts = contact_finder.find(company, domain)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, "company": company, "domain": domain,
                "contacts": [vars(c) for c in contacts]}

    @app.post("/api/outreach/draft")
    def outreach_draft(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Draft an evidence-bound outreach email for a job + contact."""
        job_id = str(body.get("job_id", ""))
        job = next((j for j in state.jobs if j["job_id"] == job_id), {})
        doc = state.documents.get(job_id, {})
        if not job and not doc:
            raise HTTPException(status_code=404, detail="unknown job_id")
        from jobhunt.integrations.enrichment import Contact, draft_outreach
        from jobhunt.llm.callbacks import resume_callback
        from jobhunt.llm.factory import build_llm_client_from_env
        contact = Contact(name=body.get("contact_name", ""),
                          email=body.get("contact_email", ""),
                          title=body.get("contact_title", ""))
        llm_client = build_llm_client_from_env()
        llm = resume_callback(llm_client) if llm_client is not None else None
        return {"ok": True, **draft_outreach(state.user_profile, job, doc, contact, llm=llm)}

    @app.post("/api/inbox/sync")
    async def inbox_sync_now(state: DashboardState = Depends(get_state)) -> dict:
        if inbox_source is None:
            raise HTTPException(
                status_code=400,
                detail="inbox not configured — set JOBHUNT_IMAP_HOST/USER/PASSWORD",
            )
        state.bus.set_loop(asyncio.get_event_loop())
        res = await asyncio.to_thread(
            sync_inbox, state, inbox_source, since=_inbox_since["ts"],
        )
        _inbox_since["ts"] = time.time()
        return res

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
        # Auto-apply: approving marks the job Applied and, for connected ATS
        # boards, fires a real submission via the submitter registry.
        submission = None
        if decision == "approve":
            job = next((j for j in state.jobs if j["job_id"] == req.job_id), None)
            doc = state.documents.get(req.job_id)
            if job is not None and job.get("status") == "Saved":
                job["status"] = "Applied"
            _add_event(state, req.job_id, "Approved",
                       f"Resume approved by {reviewer or 'you'}")
            submission = _auto_apply(state, registry, req, job, doc)
        elif decision == "reject":
            _add_event(state, req.job_id, "Rejected", "Resume rejected — won't be used",
                       status="failed")
        state.bus.publish("approval", req.job_id,
                          f"{req.company} → {decision} by {reviewer or 'anon'}")
        state.persist()
        return {"ok": True, "request": req.to_dict(), "submission": submission}

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

    # Modern frontend assets + client-routed pages (/_next, /dashboard/, …).
    # Mounted LAST so every explicit API / companion / page route above wins;
    # this catch-all only serves unmatched GETs from the static export.
    if _frontend_built:
        app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True),
                  name="frontend")

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


