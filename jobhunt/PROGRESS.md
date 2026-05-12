# JobHunt — progress tracker

Living log of what's done, what's in flight, and what's next. The
roadmap is anchored to the phases in `ARCHITECTURE.md` §6.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

---

## Phase 0 — Foundation  ·  ✅ shipped

- [x] Architecture doc with system diagram, per-agent reasoning
      pipelines, tech-stack rationale and 5-phase roadmap.
- [x] Typed data models (User, Plan, JobPosting, Company, Application,
      ReasoningTrace, ToolCall) — stdlib dataclasses, swap to Pydantic
      + SQLAlchemy in Phase 1.5.
- [x] BaseAgent contract: `deliberate → act → critique → decide` with
      bounded refinement, mandatory ReasoningTrace, PII redaction.
- [x] Tool wrapper: retry with exponential backoff, per-tool circuit
      breaker, typed `(result, degraded)` fallback.
- [x] Append-only TraceStore + async ThoughtBus (powers the dashboard
      thought stream).
- [x] Strategy & Planning Agent — produces typed execution graph from
      UserProfile.
- [x] Job Discovery Agent — dedupe by `(company,title,location)`
      fingerprint, cosine relevance, ghost-job scoring.
- [x] Pluggable `JobSource` adapter protocol + offline FixtureSource.
- [x] Company Vetting Agent (heuristic) with explainable scorecards.
- [x] Resume Architect Agent — JD keyword extraction, evidence mapping
      (every bullet must cite a graph node), no-hallucination critique.
- [x] Application Submission Agent — ATS routing
      (Greenhouse/Lever/Ashby/Workday/iCIMS/manual).
- [x] Progress Tracking Agent — email classifier, pipeline state
      transitions.
- [x] Continuous Improvement Meta-Agent — observes cycle, emits tuning
      suggestions.
- [x] Orchestrator — plan-and-execute walk with re-plan on empty
      discovery.
- [x] FastAPI dashboard skeleton + mobile-first HTML client with
      WebSocket thought stream, Kanban, one-click approve/reject.
- [x] CLI: `python -m jobhunt {demo,serve}`.
- [x] 22 unit/agent tests, all green.

---

## Phase 1 — Real ATS adapters  ·  ✅ shipped (core)

Goal: real jobs flowing through the pipeline. Tests stay offline by
parsing recorded HTTP responses.

- [x] `HTTPClient` abstraction (urllib-based, injectable for tests).
- [x] Greenhouse public-board adapter
      (`boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`)
      with HTML-to-text JD stripping.
- [x] Lever public-postings adapter
      (`api.lever.co/v0/postings/<company>?mode=json`) — handles ms
      timestamps and `categories.location`.
- [x] Ashby job-board adapter
      (`api.ashbyhq.com/posting-api/job-board/<company>`) — parses
      `compensationTierSummary` into a salary band.
- [x] Shared `passes_local_filters` helper — keeps role/location
      semantics identical across FixtureSource and real adapters.
- [x] Recorded JSON fixtures per source for offline tests.
- [x] Demo CLI: `python -m jobhunt demo --live-ats --greenhouse acme
      --lever northwind --ashby contoso`.
- [ ] Adapter-level rate limiting + polite backoff per source.
- [ ] Indeed RSS adapter (lower priority — fewer high-signal jobs).
- [ ] LinkedIn adapter — defer; ToS-sensitive, needs the human-assist
      path rather than scraping.

---

## Phase 1.5 — Persistence  ·  ✅ shipped

- [x] SQLAlchemy ORM models for all tables (User, Plan, PlanStep, Company,
      JobPosting, Application, TailoredDocument, ReasoningTrace, ToolCall).
- [x] Alembic migration framework with 2 migrations (initial schema +
      embedding columns).
- [x] Postgres-backed TraceStore replacing in-memory implementation;
      supports both SQLite (dev/tests) and Postgres (prod).
- [x] Redis client abstraction (RedisClient + FakeRedisClient) for queues,
      pub/sub, caching. 11 integration tests validating all operations.
- [x] S3 client abstraction (S3Client + FakeS3Client) for artifact storage
      (resumes, cover letters). 11 integration tests with mock.
- [x] Placeholder embeddings module with vector similarity helper
      (Phase 2: Anthropic embedding API). 6 tests validating cosine similarity.
- [x] Total 63 tests passing: 41 core + 6 DB + 6 embeddings + 11 Redis + 11 S3.
- [ ] pgvector extension on Postgres (upgrade from JSON columns in Phase 2).
- [ ] Human-in-the-loop UI for approving/rejecting tailored documents before submission.

---

## Phase 2 — Resume safety pipeline  ·  ✅ shipped (core)

- [x] Real JD parser (`jobhunt/jd_parser.py`): HTML strip, TF-IDF +
      frequency union, ATS categorisation, section splitter. 9 tests.
- [x] Templated resume engine (`jobhunt/resume_template.py`) with
      slot-fill API. Every bullet has an `evidence_id`; LLM tone-rewrite
      callback is best-effort and can't detach a bullet from evidence.
- [x] WeasyPrint PDF + python-docx renderers
      (`jobhunt/resume_renderer.py`). Text + HTML always available; PDF
      and DOCX raise `RendererUnavailable` when system deps missing.
- [x] Inter-agent peer critique (`jobhunt/agents/peer_critique.py`).
      Culture-alignment, keyword density, evidence diversity → verdict
      `ship | hold | rework` with surfaced flags. 7 tests.
- [x] Human one-click approval workflow (`jobhunt/approval.py`).
      `ApprovalQueue` state machine, Redis pub/sub fan-out, dashboard
      `/api/approvals` + `/api/approve/{id}` endpoints. 20 tests
      (12 unit + 8 HTTP).
- [x] Dashboard client.html "Awaiting your approval" panel with
      approve / reject / request-edits buttons.
- [ ] Skill-graph editor on the dashboard (deferred — needs onboarding flow).
- [ ] Anthropic SDK integration for LLM body + critique (Sonnet 4.6 + Opus 4.7).

---

## Phase 2.5 — Onboarding + Live Pipeline  ·  ✅ shipped

- [x] Multi-step onboarding wizard in the dashboard (4 steps):
      Basic info → Resume paste → Preferences → Review + launch.
- [x] Chip-input UI for roles, locations, skills, vetoed companies.
- [x] `POST /api/onboarding/profile` — validates and persists UserProfile.
- [x] `POST /api/onboarding/resume` — parses pasted resume text via TF-IDF
      vocabulary; extracts skills + job titles + years of experience;
      merges into active profile.
- [x] `POST /api/hunt/start` — fires background orchestrator task
      (``asyncio.to_thread`` so the event loop stays responsive).
- [x] `GET /api/status` — hunt lifecycle (idle / running / complete / failed).
- [x] Thread-safe ThoughtBus: ``set_loop`` + ``call_soon_threadsafe`` so
      agent thoughts published from a worker thread reach WebSocket subscribers.
- [x] Background runner hydrates DashboardState (jobs, applications, approval
      queue) as each orchestrator stage completes.
- [x] Dashboard auto-resumes to live view on page reload if hunt already
      running (polls `/api/status` on init).
- [x] `jobhunt/onboarding.py` — resume parser + profile builder helpers.
      8 tests.
- [x] Dashboard API tests expanded to 23 tests covering all new endpoints.
- [x] Total: 132 tests, all passing.

---

## Phase 3 — Submission + Tracking

- [ ] Greenhouse / Lever auto-apply via official endpoints.
- [ ] Playwright auto-fill with one-click assist fallback.
- [ ] Gmail/IMAP watcher; Calendar API integration.
- [ ] Realtime Kanban updates (already on the wire; need DB persistence
      to survive restarts).

---

## Phase 4 — Vetting + Meta-agent

- [ ] Glassdoor / Crunchbase / News / Layoffs.fyi enrichers.
- [ ] Weighted RiskRewardScorecard with user-tunable weights.
- [ ] Action log → meta-agent → prompt/parameter A/B with rollback.

---

## Phase 5 — Hardening

- [ ] OpenTelemetry tracing across services.
- [ ] HashiCorp Vault for OAuth/IMAP tokens.
- [ ] k8s manifests + autoscaling + SLOs.
- [ ] Pen test + GDPR audit (export/delete endpoints).

---

## Cross-cutting / always on

- [ ] CI workflow (`pytest` + `ruff` + `mypy`).
- [ ] Test coverage ≥ 80 % per module; ≥ 90 % on resume agent.
- [ ] Structured logging with the PII redactor on every sink.
- [ ] Containerise each agent service for prod.

---

## Recent commits (most recent first)

**Phase 2.5 — Onboarding + live pipeline** (in flight):
- 4-step onboarding wizard, resume skill extraction, background orchestrator,
  thread-safe ThoughtBus, 132 tests passing.

**Phase 2 — Resume safety pipeline**:
- README, peer critique, approval workflow, dashboard UI integration
- `268e051` Phase 2: JD parser (TF-IDF) + templated resume engine + PDF/DOCX renderers

**Phase 1.5 — Persistence layer** (4 commits):
- `d3312fc` Add S3 client abstraction for artifact storage (Phase 1.5 complete)
- `3d9f55c` Add Redis client abstraction for queues, pub/sub, and caching
- `30dbee2` Add pgvector support and embedding infrastructure for Phase 2
- `c89e42a` Add Phase 1.5 Postgres persistence layer with SQLAlchemy and Alembic

**Phase 1 — Real ATS adapters** (1 commit):
- `7b816c1` Phase 1: real Greenhouse / Lever / Ashby adapters

**Phase 0 — Foundation** (1 commit):
- `72bf5ed` Add JobHunt — multi-agent job hunting platform (Phase-0 MVP)
