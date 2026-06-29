# JobHunt — production-grade multi-agent job hunting platform

A self-driving job application platform built on **seven specialised agents**
that plan, discover, vet, tailor, submit, track, and continuously improve a
job hunt. Every decision is recorded as an inspectable
**ReasoningTrace** and streamed to a mobile-first dashboard via WebSockets.

## Live site & deploy

| What | Where |
|------|-------|
| **Progress tracker** (single source of truth — every phase/feature/task, animated) | Pages: `/tracker/` · server: `/tracker` |
| **Interactive app** (full dashboard UX on sample data, mobile-friendly) | Pages: `/site/app.html` · server: `/app` |
| **Cinematic demo** (animated 3D walkthrough + MP4) | Pages: `/demo/` · server: `/demo` |

* **Static site (mobile link):** published to GitHub Pages by `.github/workflows/pages.yml` →
  `https://iamsadat.github.io/bot/`. It bundles the tracker, interactive app and cinematic demo
  into one mobile-accessible site (auto-enables Pages on first run).
* **Backend (live dashboard):** `python -m jobhunt serve` locally, or one-click host with the
  bundled `Dockerfile` + `render.yaml` (serves `uvicorn jobhunt.dashboard.app:app`). Persistence
  path is `JOBHUNT_DB_PATH`; set `GEMINI_API_KEY` (free tier) or `ANTHROPIC_API_KEY`
  (paid) to enable real LLM bullet tone-polish on the resume pipeline.

## Deploy your own live instance

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/iamsadat/bot)

One click deploys the real FastAPI dashboard — onboarding, live multi-agent hunt,
Kanban tracking, document approval — under your own free Render account, with no
code changes. Render reads `render.yaml` and provisions everything automatically.

**To share it with a small group of real testers (recommended for an initial test):**
1. After the first deploy finishes, open the service in the Render dashboard → **Environment** → set `JOBHUNT_ACCESS_CODE` to a short passcode of your choice → save (Render redeploys automatically).
2. Share the live URL + that passcode with up to ~10 testers. Each tester's browser gets
   its own isolated workspace automatically (cookie-based) — they won't see each other's
   data.
3. This is a real-user **test** deployment, not yet built for scale: the free tier's disk
   is ephemeral (a redeploy or a long idle period resets stored data) and there's no
   horizontal scaling. For anything beyond a short test, attach a persistent disk or set
   `DATABASE_URL` to a real Postgres instance, and budget time for the still-open hardening
   items tracked in `jobhunt/tracker/tasks.json` (OpenTelemetry tracing, Vault-backed
   secrets, k8s manifests + autoscaling, a formal pen test / GDPR audit).

The static demo (landing page, sample-data interactive app, cinematic walkthrough,
live progress tracker) is already deployed for free on GitHub Pages and needs no
action — see the link at the top of this README.

## Deploy your own live instance

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/iamsadat/bot)

One click deploys the real FastAPI dashboard — onboarding, live multi-agent hunt,
Kanban tracking, document approval — under your own free Render account, with no
code changes. Render reads `render.yaml` and provisions everything automatically.

**To share it with a small group of real testers (recommended for an initial test):**
1. After the first deploy finishes, open the service in the Render dashboard → **Environment** → set `JOBHUNT_ACCESS_CODE` to a short passcode of your choice → save (Render redeploys automatically).
2. Share the live URL + that passcode with up to ~10 testers. Each tester's browser gets
   its own isolated workspace automatically (cookie-based) — they won't see each other's
   data.
3. This is a real-user **test** deployment, not yet built for scale: the free tier's disk
   is ephemeral (a redeploy or a long idle period resets stored data) and there's no
   horizontal scaling. For anything beyond a short test, attach a persistent disk or set
   `DATABASE_URL` to a real Postgres instance, and budget time for the still-open hardening
   items tracked in `jobhunt/tracker/tasks.json` (OpenTelemetry tracing, Vault-backed
   secrets, k8s manifests + autoscaling, a formal pen test / GDPR audit).

The static demo (landing page, sample-data interactive app, cinematic walkthrough,
live progress tracker) is already deployed for free on GitHub Pages and needs no
action — see the link at the top of this README.

## What's new in this drop

* **Profile import** (`jobhunt/integrations/github.py`, `jobhunt/onboarding.py`) — upload a **PDF/DOCX/TXT résumé** (base64 JSON, no multipart dep; PDF needs `pip install jobhunt[pdf]`) to auto-fill the structured builder, and **import your GitHub** public repos as Project entries (forks skipped, language+topics → skills). New `POST /api/profile/parse-resume-file` + `POST /api/profile/import-github`; onboarding gains Upload + Import-GitHub buttons.
* **More job sources** (`jobhunt/adapters/`) — keyless ATS adapters **Recruitee, Workable, Personio** (configured as company slugs in ATS settings) plus native-search aggregators **Adzuna** and **USAJobs** (env API keys), on top of Greenhouse/Lever/Ashby/Indeed. The HTTP client now supports per-call auth headers; every adapter stays offline-testable via `FakeHTTPClient`.
* **Modern Next.js + Three.js frontend** (`frontend/`) — a new App-Router SPA (TypeScript + Tailwind + Framer Motion + react-three-fiber) with an animated 3D landing hero, an animated dashboard (stat counters, pipeline kanban, autonomy controls, a **live agent-reasoning feed**, and a client-side résumé-preview drawer with PDF/DOCX/HTML downloads), and a paste-to-prefill résumé builder. It static-exports and is served by the same FastAPI app under **`/app`** (`npm run build` → `frontend/out`; the Dockerfile builds it automatically). The legacy single-file SPA stays at `/`.
* **Real, template-quality résumés** (`jobhunt/resume_template.py`, `jobhunt/resume_renderer.py`) — `UserProfile` now holds **structured Experience / Education / Projects / Links**, parsed from a pasted résumé and editable in the builder. `build_tailored_resume()` produces a single-column résumé from your **real history** — bullets reordered by JD relevance, entries reverse-chronological, **every bullet backed by an `evidence_id`** (no invented skills) — rendered to a clean PDF/DOCX/HTML matching a professional template (centered name+contact, section rules, right-aligned dates/links). Optional Gemini polish; deterministic fallback.
* **Continuous discovery + fully-autonomous auto-apply** (`jobhunt/dashboard/server.py`) — a background sweep (`JOBHUNT_DISCOVERY_POLL_SECONDS>0`, single-tenant serve) that **merges** new postings by fingerprint and never clears, plus a capped autonomous auto-apply (`auto_apply` + connected ATS + relevance floor + per-day cap) that submits without manual approval — fixtures still never POST. Shortlist size (`JOBHUNT_SHORTLIST_CAP`, default 10) and vetting threshold are now configurable. New `POST /api/discover`, `GET/POST /api/autonomy`.
* **Substantive reasoning trace** (`jobhunt/models.py`, `jobhunt/agents/base.py`) — the "agent thoughts" feed is now genuine reasoning: each agent emits structured `TraceEvent`s (what it **considered**, what it **rejected** and **why**, **confidence**, **decision**), surfaced over `/ws/stream` and grouped at `/api/activity`.
* **Recruiter-email auto-status** (`jobhunt/dashboard/inbox_sync.py`) — set `JOBHUNT_IMAP_*` and the app polls your inbox, classifies recruiter mail (interview / assessment / offer / rejection), matches it to a job by company/sender, and **auto-advances that application's status** with a timeline event and any Calendly/Zoom interview time. Off until configured; creds stay in env. A "Check email" button triggers a sync on demand.
* **Full real auto-submit** (`jobhunt/submitters/`) — auto-apply now sends a **real rendered PDF** (was plain text mislabeled as PDF), answers Greenhouse **custom screening questions** (work authorization, sponsorship, years, LinkedIn…) from a new Profile *Screening answers* section, sends browser-like headers, and surfaces the real API error on failure.
* **Application-tracking USP** (`jobhunt/dashboard/`) — a dedicated **Applications** tab: a sortable/filterable table (company / role / status / source / submitted / **days-in-stage** with stale-flagging / next action) plus per-application notes & next-step in the drawer.
* **Relevance maximization** (`jobhunt/agents/discovery.py`, `jobhunt/skills_taxonomy.py`, `jobhunt/agents/resume.py`) — discovery `relevance()` now blends lexical bag-of-words over **synonym-expanded** skills/roles with **semantic** cosine in the hashing-trick embedding space (`jobhunt/embeddings.py`), so related jobs rank higher and alias forms ("k8s" ↔ "kubernetes", "go" ↔ "golang") count. Résumé ATS coverage uses the TF-IDF/frequency JD parser (`jobhunt/jd_parser.py`) filtered to real skills and the same synonym matching — e.g. a JD that says "k8s/psql/pytorch" now matches a "kubernetes/postgresql/machine-learning" profile, raising measured coverage.
* **Editable preferences** — the Profile tab now edits **culture keywords** and **connected ATS boards** (Greenhouse/Lever/Ashby) after onboarding, not just during the wizard — so you can connect a board to enable auto-submit without restarting.
* **Auto-apply on approve** (`jobhunt/dashboard/server.py`) — approving a tailored résumé now fires a real application through the existing `SubmitterRegistry` (Greenhouse / Lever apply APIs) when you've connected those boards, records the confirmation id, and advances the approval to `SUBMITTED`. Submission is gated on having real ATS handles configured (so offline fixtures never POST), is duplicate-safe, and falls back to "marked Applied — finish on the company site" for jobs without an apply API. An optional `phone` profile field feeds the application payload.
* **Per-application progress timeline** — every job now carries a lifecycle event log (Discovered → Tailored → Approved → Submitted / status changes), exposed at `GET /api/jobs/{id}/timeline` and rendered as a **Progress** timeline in the job drawer, with a "✓ Submitted" badge + confirmation id on submitted cards.
* **Dashboard UX overhaul** (`jobhunt/dashboard/`) — the live app now has tabbed **Pipeline / Profile / Activity** views: an editable profile (`GET`/`PUT /api/profile`), a full activity log of every agent + user action (`GET /api/activity`), an LLM-status pill (Gemini/Claude polish vs. heuristic), a persisted dark/light theme, and clearer approve feedback that explains JobHunt never auto-submits. The dev-only Tracker/Demo nav is gated behind `JOBHUNT_DEV_NAV` (on for local `serve`, off in production).
* **Pure-Python document downloads** — résumé/cover-letter PDF rendering moved from WeasyPrint to **fpdf2** (`pip install jobhunt[pdf]`), so PDF works with a plain `pip` on Windows and in Docker with no system libraries; DOCX via `python-docx` and styled HTML share one structured renderer (`jobhunt/resume_renderer.py`).
* **Indeed RSS adapter** (`jobhunt/adapters/indeed.py`) — fourth job source, with polite token-bucket rate limiting (`jobhunt/rate_limit.py` + `RateLimitedHTTPClient`).
* **LLM integration** (`jobhunt/llm/`) — optional dep; `AnthropicLLMClient` (Sonnet 4.6) or `GeminiLLMClient` (free-tier `gemini-3.5-flash`) polish résumé bullet tone on top of the deterministic, evidence-backed text — `build_llm_client_from_env()` picks `GEMINI_API_KEY` over `ANTHROPIC_API_KEY` when set, else the pipeline stays fully heuristic; `FakeLLMClient` keeps the full suite offline; PII is redacted before every API call.
* **Auto-submit** for Greenhouse + Lever (`jobhunt/submitters/`) — `SubmissionAgent` calls real posting endpoints when `auto_submit_approved=True`; `SubmissionPlan` gains `submitted` + `submission_id` fields.
* **Inbox watcher** (`jobhunt/inbox/`) — IMAP4_SSL source, confidence-scored email classifier, Calendly/Zoom/datetime calendar-hint extractor (`FakeInboxSource` for offline tests).
* **Vetting enrichers** (`jobhunt/enrichers/`) — Glassdoor / Crunchbase / News / Layoffs heuristic enrichers + user-tunable weighted scorecard on `VettingAgent`.
* **A/B experiment framework** (`jobhunt/ab.py`) — deterministic bucket assignment, winner promotion, rollback; wired into the Continuous-Improvement Meta-Agent.
* **Structured logging + CI** — PII-redacting log module (`jobhunt/log.py`) and GitHub Actions workflow (`.github/workflows/ci.yml`).

---

It is designed to be:

* **Safety-first** — every résumé bullet must be backed by an
  ``evidence_id`` from the user's experience graph. The Resume Architect
  refuses to ship a draft with even one unsupported claim.
* **Observable** — the ``deliberate → act → critique → decide`` loop is
  recorded for every agent invocation, PII is redacted before storage,
  and the dashboard replays the full thought stream as it happens.
* **Failure-tolerant** — every tool call is wrapped with retry,
  exponential backoff, and a per-tool circuit breaker. Adapters degrade
  gracefully (a dead source ⇒ flag on the discovery batch, not a crash).
* **Pluggable** — swap the in-memory store for Postgres, fixture
  adapters for real Greenhouse/Lever/Ashby APIs, the placeholder
  embedder for Anthropic's embedding API. The interfaces don't change.
* **Offline-by-default for tests** — every external dependency
  (Postgres, Redis, S3, ATS APIs, LLM, inbox) ships with a `Fake*` companion
  so the ~180 tests run with zero network access.

---

## Quick start

```bash
# 1. Python 3.11+; install dependencies
pip install -r requirements.txt

# 2. Run the full offline demo (plan → discover → vet → tailor → submit)
python -m jobhunt demo

# 3. Or hit the real public ATS APIs for a single example each
python -m jobhunt demo --live-ats \
    --greenhouse stripe \
    --lever netflix \
    --ashby ramp

# 4. Launch the dashboard (mobile-first, dark + light themes)
python -m jobhunt serve --host 127.0.0.1 --port 8765
# Then open http://127.0.0.1:8765 in any browser.

# 5. Run the test suite
pytest -q
```

The demo command prints the plan, the deduped discovery batch, vetting
scorecards, tailored résumés (with keyword coverage), submission packages,
and the redacted reasoning traces — all in a few seconds, all offline.

---

## Installation

### 1. System requirements

* Python 3.11+
* Optional production extras:
  * **PostgreSQL 14+** for the persistence layer
  * **Redis 6+** for queues and pub/sub
  * **S3 / MinIO** for résumé and cover-letter artefacts
  * **WeasyPrint** system deps (`libpango`, `libcairo`) for PDF rendering

For the offline demo and tests, none of the production extras are required —
fake clients ship in the package.

### 2. Set up the project

```bash
git clone https://github.com/iamsadat/bot.git
cd bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. (Optional) Bring up Postgres + Redis + MinIO

```bash
docker compose up -d  # if a compose file is provided; otherwise install locally
export DATABASE_URL=postgresql://jobhunt:jobhunt@localhost:5432/jobhunt
export REDIS_URL=redis://localhost:6379/0
export S3_BUCKET=jobhunt-artifacts
```

### 4. Apply database migrations

```bash
alembic upgrade head
```

Alembic auto-detects `DATABASE_URL`; with no URL set, the engine defaults to
SQLite in-memory so the dashboard and tests still run.

### 5. Verify everything works

```bash
pytest -q                       # 82+ tests should pass
python -m jobhunt demo          # end-to-end offline pipeline
python -m jobhunt serve         # open http://127.0.0.1:8765
```

---

## Architecture at a glance

```
                              ┌────────────────┐
                              │   Orchestrator │
                              │  plan-and-     │
                              │  execute graph │
                              └───────┬────────┘
                                      │
   ┌─────────────┬──────────┬─────────┴─────────┬────────────┬─────────────┐
   ▼             ▼          ▼                   ▼            ▼             ▼
Strategy &   Discovery   Vetting           Resume        Submission   Tracking
Planning     (dedupe,    (risk/reward      Architect     (route to    (email
             relevance,  scorecard)        (evidence-    Greenhouse/  classifier,
             ghost-score)                  bound bullets) Lever/Ashby) Kanban)
                                                                          │
                                                                          ▼
                                                                  Continuous-
                                                                  Improvement
                                                                  Meta-Agent
```

Every agent inherits from `BaseAgent[I, O]` which enforces the contract:

1. **`deliberate(inputs, trace) → list[str]`** — bullet the plan.
2. **`act(inputs, trace) → O`** — do the work.
3. **`critique(inputs, output, trace) → dict[str, float]`** — score the output.
4. **`decide(...) → tuple[str, float]`** — final decision + confidence.

If the critique falls below the agent's `quality_threshold` (e.g. 0.8 for the
Resume Architect), the loop refines up to `max_refinements` times before
giving up. The Resume Architect scores `no_hallucination = 0` if a bullet
ever lacks an `evidence_id`, which short-circuits the loop and prevents the
draft from leaving the agent.

---

## What's shipped

| Phase | Status | Highlights |
| ----- | ------ | ---------- |
| 0 — Foundation | ✅ shipped | 7 agents, Orchestrator, FastAPI dashboard, CLI, 22 tests |
| 1 — ATS adapters | ✅ shipped | Greenhouse/Lever/Ashby + Indeed adapters, rate limiting, offline fixtures |
| 1.5 — Persistence | ✅ shipped | SQLAlchemy + Alembic, Postgres TraceStore, Redis & S3 clients (real + fake) |
| 2 — Resume safety | ✅ shipped | TF-IDF JD parser, slot-fill template, PDF/DOCX renderers, Anthropic LLM integration |
| 2.5/2.6 — Onboarding + UI | ✅ shipped | Live pipeline, premium UI, SQLite persistence, 158 tests |
| 3 — Submission + Tracking | 🟡 partial | Greenhouse/Lever auto-submit, IMAP inbox watcher; Playwright + Calendar API deferred |
| 4 — Vetting + Meta-agent | 🟡 partial | Glassdoor/Crunchbase/News/Layoffs enrichers, weighted scorecard, A/B framework |
| 5 — Hardening | not started | OpenTelemetry, Vault, k8s manifests, GDPR audit |

See `jobhunt/PROGRESS.md` for the full living checklist.

---

## CLI reference

```text
python -m jobhunt --help

Usage: python -m jobhunt {demo,serve}

Commands:
  demo    Run a full plan → discover → vet → tailor → submit cycle.
  serve   Start the FastAPI dashboard (WebSocket thought stream + Kanban).
```

`demo` options:

| Flag | Meaning |
| ---- | ------- |
| `--live-ats` | Hit real Greenhouse/Lever/Ashby APIs instead of fixtures. |
| `--greenhouse BOARD_TOKEN` | Add a Greenhouse board (repeatable). |
| `--lever COMPANY` | Add a Lever company slug (repeatable). |
| `--ashby COMPANY` | Add an Ashby company slug (repeatable). |

`serve` options:

| Flag | Default | Meaning |
| ---- | ------- | ------- |
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `8765` | TCP port. |

---

## Project layout

```
jobhunt/
├── __main__.py        # `python -m jobhunt {demo,serve}`
├── models.py          # dataclasses: User, Plan, JobPosting, Company, …
├── tools.py           # retry + circuit breaker + typed (result, degraded)
├── trace.py           # ReasoningTrace, in-memory TraceStore, ThoughtBus
├── jd_parser.py       # HTML strip + TF-IDF + ATS categorisation (Phase 2)
├── resume_template.py # slot-fill engine; every bullet has evidence_id
├── resume_renderer.py # text / HTML / PDF (WeasyPrint) / DOCX (python-docx)
├── embeddings.py      # placeholder vector + cosine similarity
├── redis_client.py    # RedisClient + FakeRedisClient (queues + pub/sub)
├── s3_client.py       # S3Client + FakeS3Client (artefact storage)
├── http.py            # urllib HTTPClient + FakeHTTPClient for offline tests
├── adapters/          # JobSource protocol + Greenhouse / Lever / Ashby
├── agents/            # 7 agents + Orchestrator
├── dashboard/         # FastAPI server + mobile-first HTML client
├── db/                # SQLAlchemy ORM + Postgres TraceStore
└── fixtures/          # offline JSON for demo & tests

alembic/               # database migrations
tests/jobhunt/         # 82+ tests (unit + integration, all offline)
```

---

## Safety invariants enforced in code

| Invariant | Where it's enforced |
| --------- | ------------------- |
| No résumé bullet without an `evidence_id`. | `resume_template.build_resume_draft` + `ResumeArchitectAgent.critique`. |
| PII (emails, phone numbers) is redacted before any storage or log sink. | `trace.redact` is called by `TraceStore.append` and `PostgresTraceStore.append`. |
| Every external HTTP call uses retry + circuit breaker. | `tools.call_tool` wraps every adapter call. |
| Adapter failures degrade gracefully — partial discovery is OK. | `DiscoveryBatch.degraded_sources`; the orchestrator only re-plans on *empty* discovery. |
| Tailored documents always require human approval before submission. | `TailoredDocument.requires_human_approval = True`; the Submission Agent surfaces, never auto-fires. |
| LLM rewrites are best-effort; deterministic fallback on any exception. | `resume_template._bullet_for_keyword` swallows LLM errors. |

---

## Testing

```bash
pytest -q                                 # all tests
pytest tests/jobhunt/test_resume_template.py -v
pytest -k "jd_parser"                     # by name
```

The suite is fully offline:

* HTTP traffic is replaced by `FakeHTTPClient` with recorded JSON fixtures.
* Redis/S3 are replaced by `FakeRedisClient`/`FakeS3Client`.
* Postgres is replaced by SQLite in-memory.
* LLM calls are not made; the resume engine falls back to deterministic
  phrasing when no `llm` callback is provided.

---

## Honest caveats

* **The current demo profile is a fixture.** Real onboarding (skill graph
  editor, OAuth import from LinkedIn) is Phase 2 / Phase 3 work.
* **Auto-apply is *not* enabled by default**. Tailored documents land in
  the "awaiting human approval" bucket and never submit themselves. This
  is by design; turning it on requires the auto-apply migration in Phase 3.
* **The placeholder embedder returns zero vectors.** Phase 2 wires in
  Anthropic's embedding API; until then, cosine similarity is a placeholder
  shape, not a real signal.
* **ToS-sensitive sources are deferred.** LinkedIn scraping is explicitly
  out; the Phase-2 plan calls for an OAuth+human-assist path instead.
