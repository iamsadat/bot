"""FastAPI dashboard — onboarding + live pipeline + persistence.

Endpoints:

  GET  /                          single-page app (client.html)
  GET  /api/status                hunt lifecycle state
  POST /api/auth/request-link     send a magic-link email to recover/link a workspace
  GET  /api/auth/verify           consume a magic-link token, set the jh_ws cookie
  GET  /api/auth/status           the linked email (if any) for the current workspace
  POST /api/billing/checkout      create a Stripe Checkout session (rails — off unless set up)
  POST /api/billing/webhook       Stripe webhook receiver (checkout.session.completed → plan=pro)
  GET  /api/billing/status        current plan + whether billing is configured
  POST /api/onboarding/profile    save user info + preferences
  POST /api/onboarding/resume     parse pasted resume text → extract skills
  POST /api/onboarding/ats        save ATS handles (greenhouse/lever/ashby)
  GET  /api/profile               current profile + ATS config
  PUT  /api/profile               update profile fields
  PUT  /api/profile/structured    replace experiences/education/projects sections
  POST /api/profile/parse-resume-file
                                  parse an uploaded résumé file (base64)
  POST /api/profile/import-github import GitHub repos as project entries
  POST /api/hunt/start            kick off the background orchestrator
  POST /api/discover              run one discovery sweep now (merge, don't clear)
  GET  /api/autonomy              read auto-apply settings
  POST /api/autonomy              update auto-apply settings
  POST /api/hunt/reset            clear everything and return to onboarding
  GET  /api/plan                  current execution plan (steps + statuses)
  GET  /api/jobs                  discovered job postings (Kanban source)
  POST /api/jobs/{job_id}/status  move a job between pipeline statuses
  GET  /api/jobs/{job_id}/timeline
                                  per-job event timeline
  POST /api/jobs/{job_id}/notes   set per-application notes + next action
  GET  /api/applications          pipeline applications
  GET  /api/documents/{job_id}    fetch tailored resume + cover letter text
  GET  /api/documents/{job_id}/download
                                  download artifact (txt / pdf / docx)
  GET  /api/traces                reasoning traces (paginated)
  GET  /api/activity              raw + grouped ThoughtBus event history
  GET  /api/metrics               funnel + streak + callback-rate metrics
  POST /api/digest/send           build + (optionally) send the activity digest
  GET  /api/inbox/status          whether an email inbox source is connected
  GET  /api/notify/status         configured notification channels
  POST /api/notify/test           send a test notification to all channels
  GET  /api/google/status         Gmail send/inbox + Calendar configuration state
  POST /api/email/send            send a follow-up/thank-you email via Gmail
  POST /api/calendar/hold         create a Google Calendar event
  GET  /api/outreach/status       whether a contact-finder provider is configured
  POST /api/outreach/find         find recruiter contacts for a job/company
  POST /api/outreach/draft        draft an evidence-bound outreach email
  POST /api/autofill              browser autofill a career-page application
  GET  /api/market/status         salary/news provider configuration state
  GET  /api/salary                salary estimate for a role + location
  GET  /api/company/intel         recent company news/intel
  GET  /api/radar                 Career Radar hits + market-value history
  GET  /api/radar/settings        read Career Radar profile fields
  POST /api/radar/settings        update Career Radar profile fields
  POST /api/tools/ats-score       free, no-auth ATS keyword match score
  POST /api/publish               publish a tailored draft to a public handle
  GET  /p/{handle}                public, unauthenticated résumé page
  POST /api/pageview              record one pageview (landing/ats_tool/public_resume)
  GET  /api/pageview/stats        aggregate pageview counts (admin token)
  POST /api/waitlist              join the waitlist with a stated price preference
  GET  /api/waitlist/stats        signup totals + price-preference split (admin token)
  POST /api/interview/questions   AI interview prep — generate questions for a job
  POST /api/interview/feedback    AI interview prep — score a practice answer
  GET  /api/skills/gaps           skill-gap learning paths from missing ATS keywords
  POST /api/inbox/sync            pull new inbox messages, match to applications
  POST /api/contacts              create/update a Career CRM contact (upsert by id)
  GET  /api/contacts              list contacts (?due=true → overdue follow-ups only)
  DELETE /api/contacts/{id}       remove a contact
  POST /api/contacts/{id}/nudge   fire a follow-up notification + draft email
  GET  /api/analytics             funnel + résumé-strategy A/B experiment results
  GET  /api/approvals             approval queue
  POST /api/approve/{id}          human one-click decision
  WS   /ws/stream                 live thought stream

Endpoints marked "(admin token)" require an ``X-Admin-Token`` header matching
``JOBHUNT_ADMIN_TOKEN``; they expose aggregate business data, not per-user
data, and are closed when that env var is unset.
"""

from __future__ import annotations

import asyncio
import json
import hmac
import os
import re
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from jobhunt import company_boards
from jobhunt.ab import Experiment, ExperimentRegistry, Variant
from jobhunt.adapters.adzuna import country_for as _adzuna_country_for
from jobhunt.approval import ApprovalQueue, ApprovalState, InvalidTransition
from jobhunt.dashboard.persistence import DashboardStore, restore_approval_queue
from jobhunt.digest import build_digest
from jobhunt.metrics import compute_funnel
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
        Depends, FastAPI, Header, HTTPException, Request,
        Response as FastAPIResponse, WebSocket, WebSocketDisconnect,
    )
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles
    _FASTAPI_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:  # pragma: no cover
    _FASTAPI_IMPORT_ERROR = _exc
    Depends = FastAPI = HTTPException = Request = FastAPIResponse = None  # type: ignore
    Header = None  # type: ignore
    WebSocket = WebSocketDisconnect = None  # type: ignore
    HTMLResponse = JSONResponse = Response = None  # type: ignore


_VALID_STATUSES = {"Saved", "Applied", "Assessment", "Interview", "Offer", "Closed"}

# Cookie name for per-workspace isolation, and the strict pattern a valid
# workspace id must match (secrets.token_hex(16) → 32 lowercase hex chars).
# Anything that doesn't match this is never trusted as a filesystem path
# component (path traversal defense-in-depth).
WORKSPACE_COOKIE = "jh_ws"
_SAFE_ID_RE = re.compile(r"^[a-f0-9]{32}$")

# Free ATS-score tool: lowercase-word tokenizer for the pasted résumé text.
_ATS_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{2,}")

# Public résumé handles: lowercase slug + "-" + 6 hex chars, e.g. "ada-1a2b3c".
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# E4 — outcome analytics: a single standing A/B experiment over tailored-resume
# strategy. Variants are illustrative copy/structure strategies; the real
# difference lives in whichever resume-writing prompt/template consults
# ``doc["variant"]`` (out of scope here — this wires up assignment + outcome
# attribution end-to-end so that piece can be added later without touching
# persistence or the analytics endpoint).
_RESUME_EXPERIMENT_NAME = "resume_strategy"
_RESUME_VARIANTS = ("concise", "keyword_rich", "impact_first")


def _default_experiment_registry() -> ExperimentRegistry:
    """Build the standing ``resume_strategy`` experiment with its variants."""
    registry = ExperimentRegistry()
    registry.register(Experiment(
        name=_RESUME_EXPERIMENT_NAME,
        target="resume",
        variants=[Variant(name=v, params={}) for v in _RESUME_VARIANTS],
    ))
    return registry


# ---------------------------------------------------------------------------
# Shared state container
# ---------------------------------------------------------------------------

# Vercel sets VERCEL=1. Serverless functions freeze once they respond, so
# nothing may run in the background there; see start_hunt and /api/cron/sweep.
_SERVERLESS = bool(os.environ.get("VERCEL"))


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
    activity_days: list = field(default_factory=list)  # ISO date strings (streaks)
    market_value: list = field(default_factory=list)  # Career Radar comp history
    contacts: list = field(default_factory=list)  # Career CRM contacts
    experiments: ExperimentRegistry = field(default_factory=_default_experiment_registry)
    linked_email: str | None = None  # verified email tied to this workspace (magic link)
    billing_plan: str = "free"  # "free" | "pro" — set only by the Stripe webhook handler
    store: DashboardStore | None = None
    notifier: Any = None  # optional jobhunt.notify.Notifier (not persisted)
    # The store's stamp() as of our last load or save; see refresh().
    _seen_stamp: Any = field(default=None, repr=False)
    # Last sweep's per-source outcome, for the dashboard's source panel:
    # name → {"status": ok|degraded, "jobs": int, "checked_at": epoch}.
    # Discovery already tracked this (DiscoveryBatch.sources_used /
    # .degraded_sources) but nothing ever surfaced it, so a source that
    # silently failed looked identical to one that found nothing.
    # Deliberately NOT persisted: both describe the last sweep, not the
    # user's data, and adding snapshot columns breaks older SQLite files
    # (there is no migration for this store). Losing them on restart just
    # means an empty panel until the next fetch, and re-serving page 1 —
    # whose jobs dedupe by fingerprint anyway.
    source_status: dict[str, dict] = field(default_factory=dict)
    # Arbeitnow is the one source with real pagination; advancing this is
    # what makes a second "Fetch more" return jobs you haven't seen.
    source_page: int = 1
    # The most recent résumé parse, kept until the profile is saved. Onboarding
    # parses a CV before any profile exists, and the form has no field for
    # measured experience — so without this, everything the résumé worked out
    # was thrown away by the very next request. Transient, like the two above.
    last_resume_parse: dict = field(default_factory=dict)

    # ------------------------------------------------------------ persistence
    def persist(self) -> None:
        if self.store is None:
            return
        try:
            plan_dict = _plan_to_dict(self.plan) if self.plan else None
            self._seen_stamp = self.store.save(
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
                activity_days=self.activity_days,
                market_value=self.market_value,
                contacts=self.contacts,
                experiments=self.experiments.to_dict(),
                linked_email=self.linked_email,
                billing_plan=self.billing_plan,
            )
        except Exception:
            pass  # never let persistence block the API

    def restore(self) -> None:
        if self.store is None:
            return
        snap = self.store.load()
        if snap is None:
            return
        try:
            self._seen_stamp = self.store.stamp()
        except Exception:
            self._seen_stamp = None
        self.user_profile = snap.get("profile")
        self.jobs = snap.get("jobs", [])
        self.applications = snap.get("applications", [])
        self.documents = snap.get("documents", {})
        self.hunt_status = snap.get("hunt_status", "idle")
        self.hunt_error = snap.get("hunt_error", "")
        self.ats_config = snap.get("ats_config", {})
        self.applies_today = snap.get("applies_today", {})
        self.activity_days = snap.get("activity_days", [])
        self.market_value = snap.get("market_value", [])
        self.contacts = snap.get("contacts", [])
        self.linked_email = snap.get("linked_email")
        self.billing_plan = snap.get("billing_plan") or "free"
        experiments_snap = snap.get("experiments") or {}
        self.experiments = (
            ExperimentRegistry.from_dict(experiments_snap)
            if experiments_snap else _default_experiment_registry()
        )
        # Defensive: hunts that were mid-run when the server died → idle
        if self.hunt_status == "running":
            self.hunt_status = "idle"
        restore_approval_queue(self.approval_queue, snap.get("approvals", []))
        self._prune_orphans()

    def refresh(self) -> bool:
        """Reload if another process saved since we last loaded or saved.

        A personal deployment is two processes on one database — the laptop
        running ``jobhunt me`` and serverless instances behind the phone — and
        each holds the state in memory. Without this, whichever wrote last
        silently discarded the other's changes.
        """
        if self.store is None:
            return False
        try:
            stamp = self.store.stamp()
        except Exception:
            return False
        if stamp is None or stamp == self._seen_stamp:
            return False
        self.approval_queue._items.clear()  # restore only upserts; drop stale
        self.restore()
        return True

    def _prune_orphans(self) -> None:
        """Drop approvals/documents whose job no longer exists.

        Repairs workspaces damaged by the unstable-job_id bug, where every
        discovery sweep queued a fresh approval for the same role. Those
        stale requests are not merely cosmetic: ``_maybe_auto_apply_batch``
        resolves ``req.job_id`` against ``state.jobs`` and silently skips
        when it finds nothing, so autonomous apply stayed dead for as long
        as the orphans were around.
        """
        live = {j.get("job_id") for j in self.jobs}
        if not live:
            return  # nothing discovered yet — don't mistake empty for orphaned
        for req in list(self.approval_queue.all()):
            if req.job_id not in live:
                self.approval_queue.remove(req.request_id)
        self.documents = {
            job_id: doc for job_id, doc in self.documents.items() if job_id in live
        }


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

def adzuna_country(profile) -> str:
    """Which Adzuna country to search for this profile.

    ``ADZUNA_COUNTRY`` remains the override and the fallback; the profile's own
    locations win when they name somewhere Adzuna covers.
    """
    default = os.environ.get("ADZUNA_COUNTRY", "us")
    locations = list(getattr(profile, "locations", None) or [])
    return _adzuna_country_for(locations, default=default)


def _build_sources(ats_config: dict, page: int = 1, profile=None):
    """Construct JobSources from the user's ATS handles, plus keyless defaults.

    ``page`` advances every paginated source: Arbeitnow's page cursor, Himalayas'
    offset, Adzuna's page, and — for a workspace with no boards of its own — the
    slice of seeded company boards being searched (see
    ``jobhunt.company_boards``). A company's *own* board still has no page 2:
    Greenhouse/Lever/Ashby hand back their whole board per request, so repeat
    fetches against a connected board correctly find nothing new until that
    company posts again.

    ``profile`` supplies the candidate's locations, used to pick Adzuna's country
    so a Hyderabad user is not silently searching the US.
    """
    from jobhunt.adapters import (
        AdzunaSource, ArbeitnowSource, AshbySource, FixtureSource,
        GreenhouseSource, HimalayasSource, LeverSource, PersonioSource,
        RecruiteeSource, RemoteOKSource, USAJobsSource, WorkableSource,
    )

    # The offline test suite and the demo must never touch the network.
    if os.environ.get("JOBHUNT_OFFLINE") == "1":
        return [
            FixtureSource(name="greenhouse",
                          only_sources=["greenhouse", "ashby", "lever"]),
            FixtureSource(name="linkedin", only_sources=["linkedin"]),
            FixtureSource(name="indeed", only_sources=["indeed"]),
        ]

    def _slugs(key: str) -> list[str]:
        return [s.strip() for s in ats_config.get(key, []) if s.strip()]

    sources = []
    gh, lv, ab = _slugs("greenhouse_tokens"), _slugs("lever_slugs"), _slugs("ashby_slugs")
    rc, wk, pn = _slugs("recruitee_slugs"), _slugs("workable_slugs"), _slugs("personio_slugs")

    # A workspace that has connected nothing gets a curated slice of public
    # company boards instead of an empty result. These are first-party postings
    # with full descriptions, they need no credentials, and — because they are
    # Greenhouse and Lever URLs — they are the only postings the submitters can
    # actually apply to, which is what makes auto-apply usable by default.
    seeded_boards = not (gh or lv or ab)
    if seeded_boards:
        seeded = company_boards.page(page)
        gh, lv, ab = seeded["greenhouse"], seeded["lever"], seeded["ashby"]

    board_sources = []
    if gh:
        board_sources.append(GreenhouseSource(board_tokens=gh))
    if lv:
        board_sources.append(LeverSource(companies=lv))
    if ab:
        board_sources.append(AshbySource(companies=ab))
    if seeded_boards:
        # Seeded boards are the paginated thing here: each sweep reads a
        # different slice of the list, so "Fetch more" reaches new companies.
        # A user's *own* connected board has no next page and is left alone.
        remaining = company_boards.has_next(page)
        for src in board_sources:
            src.has_next = remaining
    sources.extend(board_sources)
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
            country=adzuna_country(profile), page=max(1, page)))
    usa_email, usa_key = os.environ.get("USAJOBS_EMAIL"), os.environ.get("USAJOBS_API_KEY")
    if usa_email and usa_key:
        sources.append(USAJobsSource(email=usa_email, api_key=usa_key))

    # Keyless public boards — these run for everyone, with or without a
    # connected ATS, so a brand-new workspace sees real jobs instead of six
    # fixture rows. Arbeitnow and Himalayas are paginated. Remote OK goes last:
    # its board is small and carries a lot of junk rows.
    sources.append(ArbeitnowSource(page=max(1, page)))
    sources.append(HimalayasSource(page=max(1, page)))
    sources.append(RemoteOKSource())
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
    """True when discovery is reading boards a submitter can actually post to.

    Auto-submission is gated on this because the offline fixtures use
    real-looking ``boards.greenhouse.io`` URLs, so without the gate approving a
    fixture job would fire a real (garbage) POST at Greenhouse.

    The seeded company boards (``jobhunt.company_boards``) are genuine
    Greenhouse and Lever boards, so they satisfy the gate too — which is what
    stops the auto-apply toggle shipping permanently greyed out with "Connect an
    ATS to enable" and no way to do so from the dashboard. Nothing is submitted
    as a result: ``auto_apply`` still defaults off with a daily cap of 0, so a
    real application only ever goes out after the user turns it on.
    """
    if any(
        state.ats_config.get(k)
        for k in ("greenhouse_tokens", "lever_slugs", "ashby_slugs")
    ):
        return True
    return os.environ.get("JOBHUNT_OFFLINE") != "1"


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


def _resume_experiment(state: DashboardState) -> Experiment | None:
    """Return the standing résumé-strategy experiment, registering it lazily.

    Defensive: older/test ``DashboardState``s may have been constructed before
    ``experiments`` existed (or with ``None``), so this never assumes the
    field is populated.
    """
    registry = getattr(state, "experiments", None)
    if registry is None:
        registry = _default_experiment_registry()
        state.experiments = registry
    exp = registry.get(_RESUME_EXPERIMENT_NAME)
    if exp is None:
        exp = _default_experiment_registry().get(_RESUME_EXPERIMENT_NAME)
        registry.register(exp)
    return exp


def _assign_variant(state: DashboardState, doc: dict) -> str:
    """Deterministically assign a tailored ``doc`` to a résumé-strategy variant.

    Stores the choice on ``doc["variant"]`` and records an impression against
    that variant in the standing experiment, so :func:`_record_outcome` can
    later attribute a success back to it. Deterministic by ``job_id`` (via
    ``Experiment.assign``), so repeated calls for the same job are stable.
    """
    exp = _resume_experiment(state)
    job_id = str(doc.get("job_id", ""))
    if exp is None:
        import hashlib
        digest = int(hashlib.md5(job_id.encode("utf-8")).hexdigest(), 16)  # noqa: S324
        variant_name = _RESUME_VARIANTS[digest % len(_RESUME_VARIANTS)]
    else:
        variant_name = exp.assign(job_id).name
    doc["variant"] = variant_name
    if exp is not None:
        # Experiment.record() always bumps impressions and only bumps
        # successes when success=True, so this is exactly "+1 impression,
        # outcome pending" — the actual success is recorded later by
        # _record_outcome() if/when the job reaches Interview/Offer.
        exp.record(variant_name, success=False)
    return variant_name


def _record_outcome(state: DashboardState, job_id: str) -> None:
    """Attribute a success to the variant assigned to ``job_id``'s document.

    Called whenever a job's status advances to Interview/Offer. No-ops when
    the job has no tailored document, the document was never assigned a
    variant (e.g. it predates this feature), the experiment is missing, or
    the outcome was already recorded for this job — so it is always safe to
    call unconditionally and repeatedly from a status-change site (a job
    that reaches Interview then later Offer must only count as one success).

    ``_assign_variant`` already recorded one impression for this variant
    (via ``Experiment.record(success=False)``), so this turns that existing
    impression into a success directly on the ``Variant`` rather than
    calling ``record()`` again — calling ``record()`` a second time would
    count a brand-new impression instead of upgrading the existing one.
    """
    doc = state.documents.get(job_id)
    if not doc:
        return
    variant_name = doc.get("variant")
    if not variant_name or doc.get("outcome_recorded"):
        return
    exp = _resume_experiment(state)
    if exp is None:
        return
    variant = next((v for v in exp.variants if v.name == variant_name), None)
    if variant is None:
        return  # unknown variant (registry/doc out of sync) — never crash a status update
    variant.successes += 1
    doc["outcome_recorded"] = True


def _record_activity(state: DashboardState) -> None:
    """Record today as an active day (dedup) — powers streaks (M0/R2).

    Appends ``date.today().isoformat()`` only when it isn't already the last
    entry, so repeated applies on the same day count once. Cheap + idempotent,
    so it's safe to call from every "actually applied" code path.
    """
    from datetime import date
    today = date.today().isoformat()
    if not state.activity_days or state.activity_days[-1] != today:
        state.activity_days.append(today)


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

    # Seniority and target roles used to be computed here and thrown away, which
    # is why a candidate with one year of experience was shown Staff roles and
    # had to type their own job titles despite the résumé naming them.
    if profile.experience_years is None and result.get("experience_years") is not None:
        profile.experience_years = result["experience_years"]
    if not profile.target_roles and result.get("inferred_titles"):
        profile.target_roles = list(result["inferred_titles"])


def _application_plan(state: DashboardState, job: dict, doc: dict) -> dict:
    """Everything a submitter or the browser filler needs for one application."""
    profile = state.user_profile
    resume_text = doc.get("resume_text", "")
    plan = {
        "url": job.get("url", ""), "job_id": job["job_id"],
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
        "links": dict(profile.links) if profile else {},
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
            heading = lines[0].strip() if lines and lines[0].strip() else job.get("company", "")
            plan["resume_pdf"] = text_to_pdf(heading, "\n".join(lines[1:]))
    except Exception:
        pass  # fpdf2 missing → submitters fall back to encoding the plain text
    return plan


def _browser_route(url: str, *, autonomous: bool) -> bool:
    """Whether this application should go through the real hosted form.

    Greenhouse and Lever only accept API applications with the *employer's*
    key, so for a candidate the hosted form is the only route that can work.
    The co-pilot needs a screen to show the filled form on; autonomy does not.
    """
    import importlib.util

    from jobhunt.personal import display_available

    if os.environ.get("JOBHUNT_OFFLINE") == "1":
        return False  # fixtures carry real-looking Greenhouse URLs
    if os.environ.get("JOBHUNT_AUTOFILL_ENABLED", "").strip().lower() not in (
            "1", "true", "yes", "on"):
        return False
    if importlib.util.find_spec("playwright") is None:
        return False
    if not autonomous and not display_available():
        return False
    try:
        from jobhunt.autofill import supports_browser_apply
    except ImportError:
        return False
    return supports_browser_apply(url)


def _mark_submitted(state: DashboardState, req, job: dict, how: str,
                    sub_id: str = "") -> None:
    job["submitted"] = True
    job["submission_id"] = sub_id
    if job.get("status") == "Saved":
        job["status"] = "Applied"
    try:
        state.approval_queue.transition(req.request_id, ApprovalState.SUBMITTED)
    except InvalidTransition:
        pass
    company, title = job.get("company", ""), job.get("title", "")
    suffix = f" (id {sub_id})" if sub_id else ""
    _add_event(state, job["job_id"], "Submitted", f"Submitted to {company} {how}{suffix}")
    state.bus.publish("submission", job["job_id"],
                      f"{company} → {title}: submitted {how}{suffix}.")
    _record_activity(state)


def _apply_in_browser(state: DashboardState, req, job: dict, plan: dict,
                      *, autonomous: bool) -> dict:
    """Fill the hosted application form. Co-pilot leaves it open for the owner
    to review and submit; autonomy submits headless."""
    from jobhunt.autofill import browser_apply

    job_id, company = job["job_id"], job.get("company", "")

    def _finished(outcome: dict) -> None:
        # Runs on the browser thread once the owner closes the window.
        if outcome.get("submitted"):
            _mark_submitted(state, req, job, "from the browser")
        else:
            _add_event(state, job_id, "Not submitted",
                       f"Browser closed without submitting to {company}. "
                       "Approve again to reopen it.", status="failed")
        state.persist()

    headless = autonomous or os.environ.get(
        "JOBHUNT_AUTOFILL_HEADLESS", "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        result = browser_apply(plan, submit=autonomous, headless=headless,
                               keep_open=not autonomous,
                               on_finish=None if autonomous else _finished)
    except Exception as exc:  # the filler must never 500 an approve
        result = {"ok": False, "detail": f"error: {exc}"}

    if autonomous and result.get("submitted"):
        _mark_submitted(state, req, job, "automatically")
        return {"submitted": True, "detail": result.get("detail", "")}
    if not result.get("ok"):
        detail = result.get("detail") or "could not fill the form"
        _add_event(state, job_id, "Fill failed",
                   f"Could not fill {company}'s form: {detail}", status="failed")
        state.bus.publish("submission", job_id, f"{company}: form fill failed ({detail}).")
        return {"submitted": False, "detail": detail}

    missing = result.get("unfilled_required") or []
    detail = (f"{company}'s application is open in your browser, filled in"
              + (f" — {len(missing)} question(s) need you: {', '.join(missing[:5])}"
                 if missing else "")
              + ". Review it and press Submit there.")
    _add_event(state, job_id, "Filled in browser", detail, status="running")
    state.bus.publish("submission", job_id, detail)
    return {"submitted": False, "copilot": True, "detail": detail,
            "unfilled_required": missing}


def _auto_apply(state: DashboardState, registry, req, job, doc,
                *, autonomous: bool = False) -> dict | None:
    """Apply for a just-approved job. Returns a status dict.

    In order: the hosted form in a browser (the only route Greenhouse and Lever
    open to candidates), then an API submitter for boards the owner holds
    credentials for, then "finish on the company site". The job is marked
    Applied only when an application was actually sent or handed to the owner
    to send — never on a failed attempt.
    """
    if job is None:
        return None
    job_id = job["job_id"]
    company, title, url = job.get("company", ""), job.get("title", ""), job.get("url", "")

    if doc is not None and not job.get("submitted") and _browser_route(
            url, autonomous=autonomous):
        return _apply_in_browser(state, req, job, _application_plan(state, job, doc),
                                 autonomous=autonomous)

    has_route = (
        doc is not None
        and not job.get("submitted")
        and _ats_connected(state)
        and registry.for_url(url) is not None
    )
    if not has_route:
        if autonomous:
            return {"submitted": False, "detail": "no automatic route for this posting"}
        if job.get("status") == "Saved":
            job["status"] = "Applied"
        _add_event(state, job_id, "Applied", "Marked Applied — finish on the company site")
        state.bus.publish(
            "submission", job_id,
            f"{company} → {title}: marked Applied "
            f"(open the posting to finish on the company site).",
        )
        _record_activity(state)
        return {"submitted": False, "manual": True,
                "detail": "Open the posting and finish the application on the company site."}

    plan = _application_plan(state, job, doc)
    try:
        result = registry.submit(plan)
        ok = bool(result and result.ok)
        sub_id = result.submission_id if result else ""
        detail = result.detail if result else "no submitter"
    except Exception as exc:  # a network/parse error must not 500 the approve
        ok, sub_id, detail = False, "", f"error: {exc}"

    if ok:
        _mark_submitted(state, req, job, "automatically", sub_id)
        return {"submitted": True, "submission_id": sub_id}

    _add_event(
        state, job_id, "Submit failed",
        f"Submission to {company} failed: {detail}", status="failed",
    )
    state.bus.publish(
        "submission", job_id, f"{company} → {title}: submission failed ({detail}).",
    )
    return {"submitted": False, "detail": detail}


def _capabilities(notifier, inbox_source) -> list[dict]:
    """What is switched on, and the one line that switches on the rest."""
    import importlib.util

    from jobhunt.llm.factory import describe_llm_from_env

    env = os.environ.get
    on = lambda k: env(k, "").strip().lower() in ("1", "true", "yes", "on")  # noqa: E731
    llm = describe_llm_from_env()
    playwright = importlib.util.find_spec("playwright") is not None
    rows = [
        ("adzuna", "Adzuna India jobs", bool(env("ADZUNA_APP_ID") and env("ADZUNA_APP_KEY")),
         "Add ADZUNA_APP_ID and ADZUNA_APP_KEY to me.env"),
        ("continuous", "Automatic job sweeps",
         int(env("JOBHUNT_DISCOVERY_POLL_SECONDS", "0") or 0) > 0
         or bool(_SERVERLESS and env("CRON_SECRET")),
         "Set CRON_SECRET (daily Vercel Cron)" if _SERVERLESS
         else "Set JOBHUNT_DISCOVERY_POLL_SECONDS (personal mode: 6h)"),
        ("browser_apply", "Fill applications in a browser",
         playwright and on("JOBHUNT_AUTOFILL_ENABLED"),
         "pip install playwright && playwright install chromium"
         if not playwright else "Set JOBHUNT_AUTOFILL_ENABLED=1"),
        ("llm", "AI writing (Claude)", bool(llm.get("active")),
         llm.get("reason") if llm.get("provider") else
         "Install Claude Code and run `claude` once to sign in (uses your Claude plan)"),
        ("notifications", "Notifications", bool(notifier and notifier.sinks),
         "Add JOBHUNT_TELEGRAM_BOT_TOKEN + JOBHUNT_TELEGRAM_CHAT_ID to me.env"),
        ("inbox", "Recruiter email tracking", inbox_source is not None,
         "Add JOBHUNT_IMAP_HOST/USER/PASSWORD (a Gmail app password) to me.env"),
    ]
    return [{"key": k, "label": label, "on": bool(v), "hint": "" if v else hint}
            for k, label, v, hint in rows]


def _is_tracked(job: dict) -> bool:
    """Whether the owner has acted on a job, so a new hunt must keep it."""
    return job.get("status", "Saved") != "Saved" or bool(job.get("submitted"))


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
        # Per-component scores, so the UI can explain the percentage instead of
        # asking the user to trust it. See agents/discovery.score.
        "score_breakdown": p.score_breakdown,
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


# --------------------------------------------------------------------------- #
# Free ATS-score tool — top-of-funnel, no auth, no stored state.
# --------------------------------------------------------------------------- #

def _score_ats(resume_text: str, jd_text: str) -> dict[str, Any]:
    """Score how many JD ATS keywords a pasted résumé covers.

    A keyword counts as matched if it (or any of its taxonomy synonyms, e.g.
    k8s ↔ kubernetes) appears among the résumé's lowercase word tokens.
    """
    from jobhunt.agents.resume import _best_keywords
    from jobhunt.skills_taxonomy import expand_term

    kws = _best_keywords(jd_text, 20)
    resume_tokens = {t.lower() for t in _ATS_TOKEN_RE.findall(resume_text)}

    matched: list[str] = []
    missing: list[str] = []
    for kw in kws:
        forms = {kw.lower()}
        try:
            forms |= {s.lower() for s in expand_term(kw)}
        except Exception:
            pass
        (matched if forms & resume_tokens else missing).append(kw)

    score = round(len(matched) / max(1, len(kws)), 3)
    return {
        "score": score,
        "matched": matched,
        "missing": missing,
        "suggestions": missing[:8],
    }


# --------------------------------------------------------------------------- #
# Public résumé pages — unauthenticated "Built with JobHunt" share links.
# --------------------------------------------------------------------------- #

def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or "resume"


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


# --------------------------------------------------------------------------- #
# Career Radar — retention alerts for roles that beat comp/title or match a
# keyword watchlist, plus a market-value (comp) tracker over time.
# --------------------------------------------------------------------------- #

_STEP_UP_TITLES = ("senior", "staff", "principal", "lead")


def _is_radar_hit(profile: UserProfile, job: dict, doc: dict | None = None) -> bool:
    """True when ``job`` is worth flagging on the user's Career Radar.

    Independently unit-testable: takes plain data in, returns a bool, no I/O.
    Any of the following trips it (radar must be enabled first):

      a) the job's salary (min or max) beats ``current_salary``, when both
         the job and the profile have a salary set;
      b) a ``radar_keywords`` term (case-insensitive) appears in the job
         title or in the tailored document's matched/missing keywords;
      c) the job is a strong match (``relevance_score >= 0.6``) AND its
         title reads as a step up (senior/staff/principal/lead) while the
         user's current title doesn't already carry that level.
    """
    if not profile.radar_enabled:
        return False

    title = str(job.get("title", ""))
    title_lower = title.lower()

    if profile.current_salary:
        salary_min = job.get("salary_min")
        salary_max = job.get("salary_max")
        beats = (salary_min and salary_min > profile.current_salary) or (
            salary_max and salary_max > profile.current_salary)
        if beats:
            return True

    if profile.radar_keywords:
        doc = doc or {}
        haystack = " ".join([
            title_lower,
            *(str(k) for k in doc.get("matched_keywords", [])),
            *(str(k) for k in doc.get("missing_keywords", [])),
        ]).lower()
        if any(kw.strip().lower() in haystack for kw in profile.radar_keywords if kw.strip()):
            return True

    if float(job.get("relevance_score") or 0.0) >= 0.6:
        current_lower = profile.current_title.lower()
        is_step_up = any(t in title_lower for t in _STEP_UP_TITLES)
        already_there = any(t in current_lower for t in _STEP_UP_TITLES)
        if is_step_up and not already_there:
            return True

    return False


def _update_market_value(state: DashboardState, salary_client) -> bool:
    """Append today's comp estimate for the user's first target role.

    No-ops (returns ``False``) when there's no salary client, no profile, or
    no target role — and dedupes so at most one entry is recorded per day.
    Returns ``True`` when a new entry was appended.
    """
    from datetime import date

    if salary_client is None:
        return False
    profile = state.user_profile
    if profile is None or not profile.target_roles:
        return False

    today = date.today().isoformat()
    if any(e.get("date") == today for e in state.market_value):
        return False

    role = profile.target_roles[0]
    location = profile.locations[0] if profile.locations else ""
    try:
        est = salary_client.estimate(role, location, adzuna_country(profile))
    except Exception:
        return False

    state.market_value.append({
        "date": today, "median": est.median, "currency": est.currency, "role": role,
    })
    return True


def _persist_tailored_docs(state: DashboardState, docs, *, task: str = "hunt-bg") -> int:
    """Store tailored docs + open approvals for any not already present.

    Idempotent per job: skipped when we already hold a document for the job,
    and — belt and braces — when the approval queue already carries a request
    for it. The second check matters because an adapter that ever hands out an
    unstable job_id would otherwise re-queue the same role on every sweep,
    which is exactly how the queue filled with duplicates before.

    Returns the count newly tailored.
    """
    new = 0
    for doc in docs:
        if doc.job_id in state.documents:
            continue
        if state.approval_queue.by_job(doc.job_id):
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
        _assign_variant(state, state.documents[doc.job_id])
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


def _sweep_budget(callback):
    """Cap the LLM calls one sweep may make; past the cap, callers get "" and
    use their deterministic text. A sweep tailors up to 25 résumés, each a
    handful of calls — on a Claude subscription that is minutes of wall time
    and real plan usage, so only the top matches (tailored first) get AI text.
    ``JOBHUNT_LLM_CALLS_PER_SWEEP``: default 40, 0 for no cap.
    """
    cap = int(os.environ.get("JOBHUNT_LLM_CALLS_PER_SWEEP", "40") or 0)
    if cap <= 0:
        return callback
    used = [0]

    def budgeted(action: str, payload: dict) -> str:
        if used[0] >= cap:
            return ""
        used[0] += 1
        return callback(action, payload)

    return budgeted


def _execute_hunt(state: DashboardState, registry=None) -> None:
    """Runs the full orchestrator pipeline synchronously (called in a thread)."""
    from jobhunt.agents.orchestrator import Orchestrator, OrchestratorInputs
    from jobhunt.llm.callbacks import resume_callback
    from jobhunt.llm.factory import build_llm_client_from_env

    sources = _build_sources(state.ats_config, profile=state.user_profile)

    assert state.user_profile is not None
    llm_client = build_llm_client_from_env()
    llm_cb = _sweep_budget(resume_callback(llm_client)) if llm_client is not None else None
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
        fresh = [_job_dict_from_posting(p) for p in batch.postings]
        tracked = [j for j in state.jobs if _is_tracked(j)]
        seen = {j["job_id"] for j in tracked} | {j.get("fingerprint") for j in tracked}
        state.jobs = tracked + [
            j for j in fresh if j["job_id"] not in seen and j["fingerprint"] not in seen]
    _record_source_status(state, batch)

    # Populate submission packages
    subs = output.results.get("submission", [])
    kept_ids = {j["job_id"] for j in state.jobs if _is_tracked(j)}
    state.applications = [a for a in state.applications if a.get("job_id") in kept_ids] + [
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
        url = job.get("url", "")
        if registry.for_url(url) is None and not _browser_route(url, autonomous=True):
            continue  # not a real-submittable board → leave for manual review
        if float(job.get("relevance_score") or 0.0) < floor:
            continue
        try:
            state.approval_queue.transition(
                req.request_id, ApprovalState.APPROVED, reviewer="auto")
        except InvalidTransition:
            continue
        _add_event(state, req.job_id, "Approved", "Auto-approved (autonomous mode)")
        res = _auto_apply(state, registry, req, job, doc, autonomous=True)
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


def _github_username(raw: str) -> str:
    """Reduce whatever the user pasted to a bare GitHub username.

    Accepts ``ada``, ``@ada``, ``github.com/ada``, ``https://github.com/ada``
    and ``https://github.com/ada/some-repo``. Normalising only in the browser
    left the API accepting ``github.com/ada`` verbatim, which requested
    ``/users/github.com/ada/repos`` and came back 404.
    """
    s = raw.strip()
    s = re.sub(r"^[a-zA-Z]+://", "", s)          # scheme
    s = re.sub(r"^(www\.)?github\.com/?", "", s)  # host
    s = s.lstrip("@").strip("/")
    return s.split("/")[0].split("?")[0]


def _advance_page(state: DashboardState, sources) -> None:
    """Move the pagination cursor on for the next fetch.

    Advance whenever the paginated source says there IS a next page — not
    only when this sweep added something. Gating on "added" deadlocks: the
    cursor stays on page 1, page 1 is entirely known by the second fetch, so
    nothing is ever added, so the cursor never moves. Wrap back to 1 at the
    end of the feed so fetching keeps working instead of running off into
    empty pages.
    """
    paginated = [s for s in sources if hasattr(s, "has_next")]
    if not paginated:
        return
    state.source_page = state.source_page + 1 if any(
        s.has_next for s in paginated) else 1


def _record_source_status(state: DashboardState, batch) -> None:
    """Snapshot each source's outcome from the discovery batch.

    ``DiscoveryAgent`` has always tracked ``sources_used`` and
    ``degraded_sources``; nothing read them, so a source that was failing
    every sweep was indistinguishable from one that simply had no matches.
    """
    if batch is None:
        return
    degraded = set(getattr(batch, "degraded_sources", []) or [])
    per_source: dict[str, int] = {}
    for p in getattr(batch, "postings", []) or []:
        per_source[p.source] = per_source.get(p.source, 0) + 1
    now = time.time()
    for name in getattr(batch, "sources_used", []) or []:
        state.source_status[name] = {
            "status": "degraded" if name in degraded else "ok",
            "jobs": per_source.get(name, 0),
            "checked_at": now,
        }


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

    sources = _build_sources(
        state.ats_config, page=state.source_page, profile=state.user_profile)
    llm_client = build_llm_client_from_env()
    llm_cb = _sweep_budget(resume_callback(llm_client)) if llm_client is not None else None
    orch = Orchestrator(state.trace_store, state.bus, llm=llm_cb)
    result = orch.run(
        OrchestratorInputs(
            profile=state.user_profile, sources=sources,
            already_tailored=set(state.documents),
        ),
        task_id="discover-bg",
    )
    output = result.output
    if output is None:
        return {"added": 0, "tailored": 0, "applied": 0}

    state.plan = output.plan
    batch = output.results.get("discovery")
    seen = len(batch.postings) if batch else 0
    added = _merge_discovered(state, batch.postings) if batch else 0
    _record_source_status(state, batch)
    _advance_page(state, sources)
    tailored = _persist_tailored_docs(state, output.results.get("resume", []),
                                      task="discover-bg")
    if added:
        _notify(state, "discovered", f"{added} new job match(es)",
                f"{tailored} tailored and ready to review.")

    radar_hits = 0
    if added and state.user_profile.radar_enabled:
        for job in state.jobs[-added:]:
            doc = state.documents.get(job["job_id"])
            if _is_radar_hit(state.user_profile, job, doc):
                job["radar_hit"] = True
                radar_hits += 1
        if radar_hits:
            _notify(state, "radar", f"{radar_hits} role(s) match your Career Radar",
                    "Open the dashboard to compare against your current role.")

    from jobhunt.integrations.market import build_salary_client_from_env
    _update_market_value(state, build_salary_client_from_env())

    applied = _maybe_auto_apply_batch(state, registry)
    state.persist()
    return {
        "added": added, "tailored": tailored, "applied": applied,
        "radar_hits": radar_hits,
        # What the UI needs to say something honest instead of nothing:
        # how many postings came back at all, and how many were already known.
        "seen": seen, "duplicates": max(0, seen - added),
        "sources": state.source_status,
    }


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
    personal: bool | None = None,
):
    if personal is None:
        from jobhunt.personal import is_personal
        personal = is_personal() and state is not None
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

    # Salary intelligence (Adzuna histogram) + company news intel (NewsAPI).
    from jobhunt.integrations.market import (
        build_news_client_from_env, build_salary_client_from_env,
    )
    salary_client = build_salary_client_from_env()
    news_client = build_news_client_from_env()

    # Submitter registry for the auto-apply flow. Defaults to real Greenhouse +
    # Lever submitters; tests inject one backed by FakePoster. Defined before
    # the lifespan so the continuous-discovery loop can reach it.
    registry = (
        submitter_registry if submitter_registry is not None
        else _default_submitter_registry()
    )

    # Public résumé pages: a small, app-scoped SQLite store (unauthenticated,
    # shared across workspaces — published handles have no per-user owner
    # check beyond "you had the job_id whose draft you're publishing").
    from jobhunt.public_store import PublicProfileStore
    public_store = PublicProfileStore(
        db_path=os.environ.get("JOBHUNT_PUBLIC_DB_PATH", "jobhunt_public.db"),
        db_url=os.environ.get("DATABASE_URL") or None,
    )

    # Email identity: magic-link tokens + email→workspace mapping, so a user's
    # workspace can follow them across devices/cookie clears (see auth.py).
    from jobhunt.dashboard.auth import EmailIdentityStore
    auth_store = EmailIdentityStore(
        db_path=os.environ.get("JOBHUNT_AUTH_DB_PATH", "jobhunt_auth.db"),
        db_url=os.environ.get("DATABASE_URL") or None,
    )

    # Top-of-funnel pageview counter: privacy-friendly by construction (no
    # IPs/user-agents/timestamps, just surface + optional ref + coarse day —
    # see jobhunt/dashboard/pageviews.py). Shared across the app like
    # public_store/auth_store — pageviews have no per-workspace owner.
    from jobhunt.dashboard.pageviews import PageviewStore
    pageview_store = PageviewStore(
        db_path=os.environ.get("JOBHUNT_PAGEVIEWS_DB_PATH", "jobhunt_pageviews.db"),
        db_url=os.environ.get("DATABASE_URL") or None,
    )

    # Phase 2 validation: waitlist + pricing-preference signups off the
    # landing page (see jobhunt/dashboard/waitlist.py). No price is decided —
    # this just measures stated preference across a few price points.
    from jobhunt.dashboard.waitlist import WaitlistStore
    waitlist_store = WaitlistStore(
        db_path=os.environ.get("JOBHUNT_WAITLIST_DB_PATH", "jobhunt_waitlist.db"),
        db_url=os.environ.get("DATABASE_URL") or None,
    )

    @asynccontextmanager
    async def lifespan(app):
        if state is not None:
            state.bus.set_loop(asyncio.get_event_loop())
        poll_task = None
        disc_task = None
        digest_task = None
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
                first_delay = int(os.environ.get(
                    "JOBHUNT_DISCOVERY_FIRST_DELAY_SECONDS", disc_interval))

                async def _disc_loop():
                    delay = first_delay
                    while True:
                        await asyncio.sleep(delay)
                        delay = disc_interval
                        await asyncio.to_thread(state.refresh)
                        if state.user_profile is None:
                            continue
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

        # Scheduled digest: off unless a non-zero interval is set (so tests +
        # the default deployment are unchanged). Best-effort, never crashes.
        if state is not None:
            digest_interval = int(os.environ.get("JOBHUNT_DIGEST_INTERVAL_SECONDS", "0"))
            if digest_interval > 0:
                async def _digest_loop():
                    while True:
                        await asyncio.sleep(digest_interval)
                        try:
                            digest = build_digest(state)
                            if state.notifier:
                                _notify(state, "digest", digest["subject"], digest["body"])
                        except Exception as exc:  # never let the loop die
                            state.bus.publish(
                                "digest", "poll", f"Digest send error: {exc!r}",
                            )

                digest_task = asyncio.create_task(_digest_loop())
        try:
            yield
        finally:
            for t in (poll_task, disc_task, digest_task):
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

    # ---------------------------------------------------------------- admin gate

    def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
        """Gate the aggregate business-stats endpoints on a shared secret.

        Waitlist signups, stated price preferences and top-of-funnel traffic
        are the product's own market-validation data — useful to a competitor
        and nobody else's business. They were readable by anyone before this.

        Deliberately closed-by-default: an unset ``JOBHUNT_ADMIN_TOKEN`` means
        403, never "open to all". A deploy that forgets to set the token loses
        access to its own dashboard, which is a loud, recoverable failure —
        the opposite mistake silently republishes the data.
        """
        expected = os.environ.get("JOBHUNT_ADMIN_TOKEN", "")
        supplied = x_admin_token or ""
        # compare_digest on both branches so a missing token costs the same as
        # a wrong one, and an unset server token can't be probed by timing.
        if not expected or not secrets.compare_digest(expected, supplied):
            raise HTTPException(status_code=403, detail="admin token required")

    # --------------------------------------------------------------- access gate

    if personal and state is not None and state.store is not None:
        @app.middleware("http")
        async def _pick_up_other_writes(request: Request, call_next):
            # Registered before the access gate, so it runs inside it.
            if request.url.path.startswith("/api/"):
                await asyncio.to_thread(state.refresh)
            return await call_next(request)

    if access_code:
        @app.middleware("http")
        async def _access_code_gate(request: Request, call_next):
            # Only the live-pipeline API is gated. The page shell and static
            # companions (/demo, /tracker, /app, /site, /walkthrough, /) must
            # always load so the gate is a soft door, not a wall around the
            # whole site.
            if request.url.path.startswith("/api/") and not (
                    request.url.path == "/api/cron/sweep"):  # checks CRON_SECRET
                supplied = (
                    request.headers.get("X-Access-Code")
                    or request.query_params.get("code")
                    or ""
                )
                if not hmac.compare_digest(supplied, access_code):
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
    def index(_state: DashboardState = Depends(get_state)):
        # Keep Depends(get_state) so the workspace cookie is still minted here.
        if personal and _frontend_built:
            # One owner has no use for the marketing landing page.
            return RedirectResponse("/dashboard/")
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
            "personal": personal,
            "capabilities": _capabilities(notifier, inbox_source),
        }

    # ---------------------------------------------------------------------- auth

    @app.post("/api/auth/request-link")
    def request_magic_link(body: dict, request: Request) -> dict:
        from jobhunt.dashboard.auth import send_magic_link_email

        email = str(body.get("email", "")).strip()
        if not email or "@" not in email:
            # Always 200 — never reveal whether an email is "valid"/known.
            return {"sent": False}

        current_ws_id = request.cookies.get(WORKSPACE_COOKIE)
        token = auth_store.create_token(email, ws_id_hint=current_ws_id)
        link = f"{str(request.base_url).rstrip('/')}/api/auth/verify?token={token}"

        sent = send_magic_link_email(email, link)
        if sent:
            return {"sent": True}
        if os.environ.get("JOBHUNT_AUTH_DEV_MODE") == "1":
            return {"sent": False, "dev_link": link}
        return {"sent": False}

    @app.get("/api/auth/verify")
    def verify_magic_link(token: str, response: FastAPIResponse) -> dict:
        if workspace_factory is None:
            return JSONResponse(
                status_code=400,
                content={"detail": "not applicable in single-tenant mode"},
            )

        payload = auth_store.consume_token(token)
        if payload is None:
            return JSONResponse(
                status_code=400, content={"detail": "invalid or expired link"},
            )

        email = payload["email"]
        ws_id = auth_store.get_workspace_for_email(email)
        if ws_id is None:
            ws_id = payload.get("ws_id_hint") or secrets.token_hex(16)
            auth_store.link_email_to_workspace(email, ws_id)

        response.set_cookie(
            WORKSPACE_COOKIE, ws_id, max_age=60 * 60 * 24 * 180,
            httponly=True, samesite="lax",
        )
        ws_state = workspace_factory(ws_id)
        ws_state.linked_email = email
        ws_state.persist()
        return {"verified": True, "email": email}

    @app.get("/api/auth/status")
    def auth_status(state: DashboardState = Depends(get_state)) -> dict:
        return {"linked_email": state.linked_email}

    # -------------------------------------------------------------- billing rails

    @app.post("/api/billing/checkout")
    def billing_checkout(
        request: Request, state: DashboardState = Depends(get_state),
    ) -> dict:
        """Create a Stripe Checkout session. Pure rails — no plan/price decided
        yet, so this just stands up the redirect; off unless STRIPE_SECRET_KEY
        and STRIPE_PRICE_ID are both set."""
        from jobhunt.dashboard.billing import build_stripe_client_from_env

        stripe_client = build_stripe_client_from_env()
        if stripe_client is None:
            raise HTTPException(status_code=400,
                                detail="billing not configured — set STRIPE_SECRET_KEY")
        price_id = os.environ.get("STRIPE_PRICE_ID")
        if not price_id:
            raise HTTPException(status_code=400,
                                detail="billing not configured — set STRIPE_PRICE_ID")

        if workspace_factory is None:
            ws_id = "single-tenant"
        else:
            ws_id = request.cookies.get(WORKSPACE_COOKIE) or "single-tenant"

        base = str(request.base_url)
        try:
            session = stripe_client.create_checkout_session(
                ws_id,
                state.linked_email,
                price_id,
                success_url=f"{base}dashboard?checkout=success",
                cancel_url=f"{base}dashboard?checkout=cancelled",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"url": session["url"]}

    @app.post("/api/billing/webhook")
    async def billing_webhook(request: Request) -> dict:
        """Stripe webhook receiver. Verifies the signature over the raw body
        (must NOT use a parsed Pydantic body — Stripe signs the raw bytes),
        then on checkout.session.completed marks that workspace's plan "pro".
        No real entitlement gating yet — this is rails only."""
        from jobhunt.dashboard.billing import verify_webhook_signature

        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        sig_header = request.headers.get("Stripe-Signature", "")
        payload = await request.body()
        if not webhook_secret or not verify_webhook_signature(
            payload, sig_header, webhook_secret,
        ):
            raise HTTPException(status_code=400, detail="invalid webhook signature")

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid JSON payload")

        if event.get("type") == "checkout.session.completed":
            obj = (event.get("data") or {}).get("object") or {}
            ws_id = obj.get("client_reference_id")
            if workspace_factory is not None and ws_id:
                ws_state = workspace_factory(ws_id)
            elif workspace_factory is None:
                ws_state = state
            else:
                ws_state = None
            if ws_state is not None:
                ws_state.billing_plan = "pro"
                ws_state.persist()

        return {"received": True}

    @app.get("/api/billing/status")
    def billing_status(state: DashboardState = Depends(get_state)) -> dict:
        from jobhunt.dashboard.billing import build_stripe_client_from_env

        configured = build_stripe_client_from_env() is not None
        return {"plan": state.billing_plan, "billing_configured": configured}

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
        # Re-apply the last résumé parse. Onboarding parses a CV first and saves
        # the form second, and the form has no field for measured experience, so
        # building a fresh profile here discarded it — which left the seniority
        # gate with no candidate level and let Staff roles back in. The merge is
        # fill-empty, so anything the user typed still wins.
        if state.last_resume_parse:
            _apply_parsed_resume(state.user_profile, state.last_resume_parse)
        state.persist()
        return {"ok": True, "user_id": state.user_profile.user_id}

    @app.post("/api/onboarding/resume")
    def parse_resume(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        text = body.get("text", "")
        if not text.strip():
            raise HTTPException(status_code=422, detail="resume text is required")
        result = parse_resume_text(text)
        # Remembered so saving the form later cannot discard it; see
        # save_profile. Onboarding parses before a profile exists.
        state.last_resume_parse = result
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
            # An uploaded but unsaved résumé, so returning to onboarding
            # restores it instead of showing an empty form.
            return {"profile": None, "pending_parse": state.last_resume_parse}
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
        # Remembered so saving the form later cannot discard it; see
        # save_profile. Onboarding parses before a profile exists.
        state.last_resume_parse = result
        if state.user_profile is not None:
            _apply_parsed_resume(state.user_profile, result)
            state.persist()
        return result

    @app.post("/api/profile/import-github")
    def import_github(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Import a GitHub user's public repos as Project entries."""
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        username = _github_username(str(body.get("username", "")))
        if not username:
            raise HTTPException(status_code=422, detail="username is required")
        from jobhunt.integrations import GitHubClient, GitHubError, repos_to_projects
        try:
            repos = GitHubClient().fetch_repos(username)
        except GitHubError as exc:
            # Every failure used to collapse into a bare 502, so "you typed the
            # username wrong" and "GitHub is rate-limiting you" looked identical.
            status = getattr(exc, "status", None)
            if status == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"No GitHub user '{username}' — check the spelling.")
            if status == 403:
                raise HTTPException(
                    status_code=429,
                    detail="GitHub rate limit reached (60 requests/hour without a "
                           "token). Wait an hour, or set GITHUB_TOKEN to raise it.")
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
        # A new hunt replaces untouched leads, never what the owner has acted
        # on: it used to wipe Applied/Interview/Offer jobs and their résumés.
        kept = [j for j in state.jobs if _is_tracked(j)]
        kept_ids = {j["job_id"] for j in kept}
        state.jobs = kept
        state.applications = [a for a in state.applications if a.get("job_id") in kept_ids]
        state.documents = {k: v for k, v in state.documents.items() if k in kept_ids}
        state.hunt_error = ""
        state.hunt_progress = {}
        # We're on the main event loop here (this is an async route handler,
        # not a threaded dependency), so it's safe to wire up cross-thread
        # publishing now, just before the orchestrator starts running in a
        # worker thread via asyncio.to_thread.
        state.bus.set_loop(asyncio.get_event_loop())
        if _SERVERLESS:
            # A serverless function is frozen once it responds, so a background
            # task would never finish. Run the hunt inside the request instead.
            await _run_hunt_bg(state, registry)
            return {"ok": True, "hunt_status": state.hunt_status}
        asyncio.create_task(_run_hunt_bg(state, registry))
        return {"ok": True, "hunt_status": "running"}

    @app.get("/api/cron/sweep")
    async def cron_sweep(request: Request) -> dict:
        """The scheduled sweep where no process stays up to run one (Vercel
        Cron). Authorised by CRON_SECRET, not the access code."""
        secret = os.environ.get("CRON_SECRET", "")
        supplied = request.headers.get("authorization", "")
        if not secret or not hmac.compare_digest(supplied, f"Bearer {secret}"):
            raise HTTPException(status_code=401, detail="unauthorized")
        if state is None:
            raise HTTPException(status_code=400, detail="personal mode only")
        await asyncio.to_thread(state.refresh)
        if state.user_profile is None:
            return {"ok": True, "skipped": "no profile yet"}
        state.bus.set_loop(asyncio.get_event_loop())
        res = await asyncio.to_thread(_discover_once, state, registry)
        if state.notifier:
            digest = build_digest(state)
            _notify(state, "digest", digest["subject"], digest["body"])
        return {"ok": True, **res}

    @app.post("/api/discover")
    async def discover_now(state: DashboardState = Depends(get_state)) -> dict:
        """Run a single continuous-style sweep now (merge, don't clear)."""
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        state.bus.set_loop(asyncio.get_event_loop())
        res = await asyncio.to_thread(_discover_once, state, registry)
        return {"ok": True, **res}

    @app.get("/api/sources")
    def get_sources(state: DashboardState = Depends(get_state)) -> dict:
        """Per-source outcome of the last sweep, for the dashboard panel."""
        return {
            "sources": [
                {"name": name, **info}
                for name, info in sorted(state.source_status.items())
            ],
            "page": state.source_page,
            # Connected boards hand back their whole board at once, so once
            # you've fetched there is nothing more until the company posts.
            # The UI uses this to explain a legitimate "0 new".
            "ats_connected": _ats_connected(state),
            # Whether those boards are ours or the user's. The UI says so, and
            # offers to connect their own — otherwise "177 public company boards"
            # looks like magic and a user with a specific employer in mind has
            # nowhere on the dashboard to add it.
            "seeded_boards": not any(
                state.ats_config.get(k)
                for k in ("greenhouse_tokens", "lever_slugs", "ashby_slugs")
            ),
            "seeded_board_count": (
                len(company_boards.GREENHOUSE)
                + len(company_boards.LEVER)
                + len(company_boards.ASHBY)
            ),
        }

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
        awaiting = {r.job_id for r in state.approval_queue.pending()}
        return {"jobs": [
            {**j, "awaiting_approval": j.get("job_id") in awaiting}
            for j in state.jobs
        ]}

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
                if new_status in ("Interview", "Offer"):
                    _record_outcome(state, job_id)
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

    # ----------------------------------------------------------------- metrics

    @app.get("/api/metrics")
    def get_metrics(state: DashboardState = Depends(get_state)) -> dict:
        return compute_funnel(state)

    # ----------------------------------------------------------------- digest

    @app.post("/api/digest/send")
    def send_digest(state: DashboardState = Depends(get_state)) -> dict:
        digest = build_digest(state)
        sent = bool(state.notifier)
        if sent:
            _notify(state, "digest", digest["subject"], digest["body"])
        return {"ok": True, "sent": sent, **digest}

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

    # ----------------------------------------------------------------- autofill

    @app.post("/api/autofill")
    def autofill(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        """Real-browser autofill for a career-page URL (co-pilot by default).

        Off unless JOBHUNT_AUTOFILL_ENABLED is set (browser automation is heavy
        + ToS-sensitive). submit=false (default) fills the form and leaves the
        final click to the user.
        """
        if os.environ.get("JOBHUNT_AUTOFILL_ENABLED", "").lower() not in ("1", "true", "yes"):
            raise HTTPException(
                status_code=400,
                detail="autofill disabled — set JOBHUNT_AUTOFILL_ENABLED=1 (needs Playwright)")
        if state.user_profile is None:
            raise HTTPException(status_code=400, detail="complete onboarding first")
        url = str(body.get("url", "")).strip()
        if not url:
            raise HTTPException(status_code=422, detail="url is required")
        from jobhunt.autofill import autofill_application
        try:
            result = autofill_application(
                url, state.user_profile,
                getattr(state.user_profile, "application_answers", {}),
                submit=bool(body.get("submit", False)),
                headless=os.environ.get("JOBHUNT_AUTOFILL_HEADLESS", "1").lower()
                in ("1", "true", "yes"))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"autofill error: {exc}")
        return {"ok": result.success, "ats": result.ats, "url": result.url,
                "filled": [f.label for f in result.filled],
                "skipped": [f.label for f in result.skipped],
                "requires_user": result.requires_user, "notes": result.notes}

    # --------------------------------------------------------------- market intel

    @app.get("/api/market/status")
    def market_status() -> dict:
        return {"salary": salary_client is not None, "news": news_client is not None}

    @app.get("/api/salary")
    def salary(role: str, location: str = "",
               state: DashboardState = Depends(get_state)) -> dict:
        if salary_client is None:
            raise HTTPException(status_code=400,
                                detail="salary intel needs Adzuna keys (ADZUNA_APP_ID/KEY)")
        if not role.strip():
            raise HTTPException(status_code=422, detail="role is required")
        try:
            # The profile's country, as job search uses: ADZUNA_COUNTRY
            # defaults to "us", so a Bangalore lookup was a 400 from Adzuna US.
            est = salary_client.estimate(role, location,
                                         adzuna_country(state.user_profile))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, **asdict(est)}

    @app.get("/api/company/intel")
    def company_intel(company: str) -> dict:
        if news_client is None:
            raise HTTPException(status_code=400,
                                detail="company news needs JOBHUNT_NEWSAPI_KEY")
        if not company.strip():
            raise HTTPException(status_code=422, detail="company is required")
        try:
            intel = news_client.company_intel(company)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, **asdict(intel)}

    # ----------------------------------------------------------------- radar

    @app.get("/api/radar")
    def get_radar(state: DashboardState = Depends(get_state)) -> dict:
        profile = state.user_profile
        return {
            "market_value": state.market_value,
            "hits": [j for j in state.jobs if j.get("radar_hit")],
            "enabled": bool(profile and profile.radar_enabled),
        }

    @app.get("/api/radar/settings")
    def get_radar_settings(state: DashboardState = Depends(get_state)) -> dict:
        profile = state.user_profile
        if profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        return {
            "radar_enabled": profile.radar_enabled,
            "current_salary": profile.current_salary,
            "current_title": profile.current_title,
            "radar_keywords": profile.radar_keywords,
        }

    @app.post("/api/radar/settings")
    def update_radar_settings(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        profile = state.user_profile
        if profile is None:
            raise HTTPException(status_code=400, detail="no profile yet — onboard first")
        if "radar_enabled" in body:
            profile.radar_enabled = bool(body["radar_enabled"])
        if "current_salary" in body:
            raw = body["current_salary"]
            profile.current_salary = int(raw) if raw else None
        if "current_title" in body:
            profile.current_title = str(body["current_title"]).strip()
        if "radar_keywords" in body:
            kws = body["radar_keywords"]
            if isinstance(kws, list):
                profile.radar_keywords = [str(k).strip() for k in kws if str(k).strip()]
            else:
                profile.radar_keywords = [s.strip() for s in str(kws).split(",") if s.strip()]
        state.persist()
        return {
            "ok": True,
            "radar_enabled": profile.radar_enabled,
            "current_salary": profile.current_salary,
            "current_title": profile.current_title,
            "radar_keywords": profile.radar_keywords,
        }

    # --------------------------------------------------------------- growth tools
    #
    # Top-of-funnel, no-auth, no-workspace-state endpoints: a free ATS-score
    # checker and public "Built with JobHunt" résumé pages. Both must work with
    # zero prior state, so neither depends on ``get_state``/a profile/a hunt.

    @app.post("/api/tools/ats-score")
    def ats_score(body: dict) -> dict:
        resume_text = str(body.get("resume_text", ""))
        jd_text = str(body.get("jd_text", ""))
        if not resume_text.strip() and not jd_text.strip():
            raise HTTPException(
                status_code=422, detail="resume_text or jd_text is required"
            )
        return _score_ats(resume_text, jd_text)

    @app.post("/api/publish")
    def publish_resume(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        job_id = str(body.get("job_id", ""))
        if not job_id:
            raise HTTPException(status_code=422, detail="job_id is required")
        doc = state.documents.get(job_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        draft = doc.get("draft")
        if not draft:
            raise HTTPException(status_code=404, detail="no résumé draft for this job")
        name = str(draft.get("candidate_name") or "resume")
        handle = f"{_slugify(name)}-{secrets.token_hex(3)}"
        public_store.publish(handle, draft)
        return {"ok": True, "handle": handle, "url": f"/p/{handle}"}

    @app.get("/p/{handle}", response_class=HTMLResponse)
    def public_resume_page(handle: str) -> str:
        draft = public_store.get(handle)
        if draft is None:
            raise HTTPException(status_code=404, detail="unknown handle")
        from jobhunt.resume_renderer import draft_to_styled_html
        from jobhunt.resume_template import ResumeDraft
        # Server-rendered HTML (not a Next.js page), so the view is recorded
        # here rather than via a client-side beacon.
        pageview_store.record(
            "public_resume", ref=handle, day=datetime.utcnow().date().isoformat(),
        )
        return draft_to_styled_html(
            ResumeDraft.from_dict(draft),
            footer="Built with JobHunt — https://github.com/iamsadat/bot",
        )

    # ------------------------------------------------------------- pageviews
    #
    # Basic, privacy-friendly pageview counter for the no-auth top-of-funnel
    # surfaces (landing page, ATS tool, public résumé pages — see
    # jobhunt/dashboard/pageviews.py for what is/isn't stored). No auth, no
    # rate limiting for v1 — abuse-resistance is a later concern, not
    # blocking this rails work.

    @app.post("/api/pageview")
    def record_pageview(body: dict) -> dict:
        from jobhunt.dashboard.pageviews import SURFACES as _PAGEVIEW_SURFACES
        surface = str(body.get("surface", ""))
        if surface not in _PAGEVIEW_SURFACES:
            raise HTTPException(status_code=400, detail="invalid surface")
        ref = body.get("ref")
        ref = str(ref) if ref is not None else None
        pageview_store.record(surface, ref=ref, day=datetime.utcnow().date().isoformat())
        return {"ok": True}

    @app.get("/api/pageview/stats", dependencies=[Depends(require_admin)])
    def pageview_stats() -> dict:
        return pageview_store.counts()

    # ------------------------------------------------------------------ waitlist

    @app.post("/api/waitlist")
    def join_waitlist(body: dict) -> dict:
        from jobhunt.dashboard.waitlist import PRICE_PREFS

        email = str(body.get("email", "")).strip()
        price_pref = str(body.get("price_pref", ""))
        if "@" not in email or price_pref not in PRICE_PREFS:
            raise HTTPException(status_code=400, detail="invalid email or price_pref")
        waitlist_store.join(email, price_pref, day=datetime.utcnow().date().isoformat())
        return {"ok": True}

    @app.get("/api/waitlist/stats", dependencies=[Depends(require_admin)])
    def waitlist_stats() -> dict:
        return waitlist_store.counts()

    # ------------------------------------------------------------------ interview

    @app.post("/api/interview/questions")
    def interview_questions(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        from jobhunt.interview import generate_questions
        from jobhunt.llm.callbacks import resume_callback
        from jobhunt.llm.factory import build_llm_client_from_env

        job_id = str(body.get("job_id", ""))
        job = next((j for j in state.jobs if j["job_id"] == job_id), None)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job_id")
        doc = state.documents.get(job_id)

        llm_client = build_llm_client_from_env()
        llm_cb = resume_callback(llm_client) if llm_client is not None else None
        questions = generate_questions(state.user_profile, job, doc, llm=llm_cb)
        return {"questions": questions}

    @app.post("/api/interview/feedback")
    def interview_feedback(body: dict) -> dict:
        from jobhunt.interview import answer_feedback
        from jobhunt.llm.callbacks import resume_callback
        from jobhunt.llm.factory import build_llm_client_from_env

        question = str(body.get("question", ""))
        answer = str(body.get("answer", ""))
        if not question.strip() or not answer.strip():
            raise HTTPException(
                status_code=422, detail="question and answer are required"
            )

        llm_client = build_llm_client_from_env()
        llm_cb = resume_callback(llm_client) if llm_client is not None else None
        return answer_feedback(question, answer, llm=llm_cb)

    # --------------------------------------------------------------------- skills

    @app.get("/api/skills/gaps")
    def skill_gaps(state: DashboardState = Depends(get_state)) -> dict:
        from jobhunt.learning import compute_skill_gaps
        return {"gaps": compute_skill_gaps(state)}

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

    # --------------------------------------------------------------------- crm
    #
    # Career CRM: lightweight contact book + overdue follow-up nudges. Plain
    # dicts in ``state.contacts`` (persisted) — no new model module needed.

    @app.post("/api/contacts")
    def upsert_contact(body: dict, state: DashboardState = Depends(get_state)) -> dict:
        email = str(body.get("email", "")).strip()
        if "@" not in email:
            raise HTTPException(status_code=422, detail="valid email is required")
        contact_id = str(body.get("id", "")).strip() or secrets.token_hex(8)
        existing = next((c for c in state.contacts if c["id"] == contact_id), None)
        record = {
            "id": contact_id,
            "name": str(body.get("name", "")).strip(),
            "email": email,
            "company": str(body.get("company", "")).strip(),
            "title": str(body.get("title", "")).strip(),
            "last_contact": body.get("last_contact") or None,
            "next_followup": body.get("next_followup") or None,
            "notes": str(body.get("notes", "")).strip(),
            "job_id": body.get("job_id") or None,
        }
        if existing is not None:
            existing.update(record)
        else:
            state.contacts.append(record)
        state.persist()
        return {"ok": True, "contact": existing if existing is not None else record}

    @app.get("/api/contacts")
    def list_contacts(
        due: bool = False, state: DashboardState = Depends(get_state),
    ) -> dict:
        from datetime import date

        contacts = state.contacts
        if due:
            today = date.today().isoformat()
            contacts = [
                c for c in contacts
                if c.get("next_followup") and str(c["next_followup"]) <= today
            ]
        return {"contacts": contacts}

    @app.delete("/api/contacts/{contact_id}")
    def delete_contact(contact_id: str, state: DashboardState = Depends(get_state)) -> dict:
        before = len(state.contacts)
        state.contacts = [c for c in state.contacts if c["id"] != contact_id]
        if len(state.contacts) == before:
            raise HTTPException(status_code=404, detail="unknown contact")
        state.persist()
        return {"ok": True}

    @app.post("/api/contacts/{contact_id}/nudge")
    def nudge_contact(contact_id: str, state: DashboardState = Depends(get_state)) -> dict:
        contact = next((c for c in state.contacts if c["id"] == contact_id), None)
        if contact is None:
            raise HTTPException(status_code=404, detail="unknown contact")
        name = contact.get("name") or contact.get("email", "this contact")
        _notify(
            state, "followup", f"Follow up with {name}",
            f"It's time to follow up with {name} ({contact.get('company', '')}).",
        )
        draft = None
        if contact.get("job_id"):
            try:
                subject, body = _email_template(state, str(contact["job_id"]), "follow_up")
                draft = {"subject": subject, "body": body}
            except Exception:
                draft = None
        return {"ok": True, "contact": contact, "draft": draft}

    # ----------------------------------------------------------------- analytics

    @app.get("/api/analytics")
    def get_analytics(state: DashboardState = Depends(get_state)) -> dict:
        exp = _resume_experiment(state)
        winner = exp.winner() if exp is not None else None
        return {
            "funnel": compute_funnel(state),
            "experiment": exp.to_dict() if exp is not None else None,
            "winner": winner.name if winner is not None else None,
            "variants": [
                {
                    "name": v.name,
                    "impressions": v.impressions,
                    "successes": v.successes,
                    "success_rate": v.success_rate,
                }
                for v in (exp.variants if exp is not None else [])
            ],
        }

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
                ws.headers.get("X-Access-Code") or ws.query_params.get("code") or ""
            )
            if not hmac.compare_digest(supplied, access_code):
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


