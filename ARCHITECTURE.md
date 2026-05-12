# JobHunt — Multi-Agent Job Hunting Platform

A production-grade, multi-agent system that plans, discovers, vets, tailors,
submits, and tracks job applications with deliberate reasoning at every step.
This document is the design contract for the system. The accompanying
`jobhunt/` Python package implements the MVP (Orchestrator + Job Discovery)
plus typed stubs and reasoning hooks for the remaining agents.

---

## 1. System overview

```
                              ┌──────────────────────────┐
                              │       Dashboard (UI)     │
                              │  FastAPI + React (mobile │
                              │  first, WCAG 2.1 AA)     │
                              └────────────┬─────────────┘
                                           │ REST + WebSocket
                                           │ (thought stream)
                              ┌────────────▼─────────────┐
                              │  API Gateway / Auth      │
                              │  FastAPI, OAuth2, RBAC   │
                              └────────────┬─────────────┘
                                           │
                              ┌────────────▼─────────────┐
                              │     Orchestrator Agent   │
                              │  Plan-and-Execute graph  │◄──────┐
                              │  LangGraph state machine │       │
                              └────┬────┬────┬────┬──────┘       │
                                   │    │    │    │              │
                       ┌───────────┘    │    │    └─────────┐    │ reflection
                       │                │    │              │    │ + replan
            ┌──────────▼───┐  ┌─────────▼─┐  │   ┌──────────▼┐   │
            │ Strategy &   │  │ Discovery │  │   │  Vetting  │   │
            │ Planning     │  │ & Intel   │  │   │ & Research│   │
            │ Agent        │  │ Agent     │  │   │ Agent     │   │
            └──────────────┘  └───────────┘  │   └───────────┘   │
                                              │                  │
                              ┌───────────────▼──┐  ┌────────────▼─┐
                              │ Resume & Cover   │  │ Application  │
                              │ Letter Architect │  │ & Submission │
                              │ Agent (+ critic) │  │ Agent        │
                              └──────────────────┘  └──────────────┘
                                                            │
                                            ┌───────────────▼────────────┐
                                            │ Progress Tracking & Comms  │
                                            │ Agent (IMAP / Gmail / Cal) │
                                            └───────────────┬────────────┘
                                                            │
                                            ┌───────────────▼────────────┐
                                            │ Continuous Improvement     │
                                            │ Meta-Agent (RLHF / prefs)  │
                                            └────────────────────────────┘

                       Shared infrastructure (all agents)
        ┌──────────────────────────────────────────────────────────────┐
        │  PostgreSQL (state)  │  Redis (queues, cache)  │  S3 (docs)  │
        │  Vector store (pgvector / Qdrant) for JD + skill graph       │
        │  Celery / BullMQ task queue   │ OpenTelemetry tracing        │
        │  Immutable reasoning-trace log (append-only, S3 + Postgres)  │
        │  Secrets / token vault (HashiCorp Vault or AWS KMS)          │
        └──────────────────────────────────────────────────────────────┘
```

### Key properties

* **Plan-and-execute** orchestration — the Orchestrator produces a typed
  execution graph (`Plan`) before any agent runs, exposes it to the user
  via the dashboard, and re-plans when reality diverges from the plan.
* **Reasoning-first** — every agent emits a structured `ReasoningTrace`
  *before* any external call or content generation. Traces are persisted
  immutably and streamed to the dashboard.
* **Self-critique** — agents run an internal critic pass against a
  per-agent checklist and refine until a quality threshold is met or a
  retry budget is exhausted.
* **Inter-agent validation** — critical artifacts (tailored resume, cover
  letter, submission package) must pass a second agent's cross-check
  before they are eligible for human approval.
* **Human-in-the-loop only at decision gates** — initial config, final
  one-click approval of generated documents, and CAPTCHA handling.

---

## 2. Agent reasoning pipelines

Every agent shares the same skeleton:

```
input → pre-action deliberation → tool plan → execute (with retries)
      → self-critique → (optional) peer critique → structured output
      → append ReasoningTrace
```

The standard `ReasoningTrace` contains: `agent`, `task_id`, `inputs_hash`,
`thoughts` (list of free-form reasoning bullets), `tool_calls` (each with
arguments, response summary, latency, retries, fallback), `self_critique`
(checklist scores), `decision`, `confidence`, `timestamp`.

### 2.1 Strategy & Planning Agent

Goal: convert user preferences into a typed execution graph.

```
1. Load profile: roles, location, salary band, culture, constraints,
   resume skill-graph.
2. Deliberate:
     - which role families align with trajectory (justify per role)
     - which industries are trending (data: BLS, Crunchbase trends)
     - which skills to emphasize this cycle
     - which sources to prioritize and how often to refresh
3. Emit JobHuntPlan { milestones, search_queries, source_priorities,
   weekly_target, stop_conditions }.
4. Self-critique: coverage, realism, diversity of sources, alignment
   with stated preferences.
5. Persist + publish to dashboard's Planning Studio.
```

### 2.2 Job Discovery & Intelligence Agent

```
1. Read JobHuntPlan.search_queries.
2. Plan query strategy: keyword combos, geo filters, posting age,
   per-source rate limits, polite scraping cadence.
3. Reflect on last run: which queries produced noise, ghost jobs, dup
   rate, click-through proxy.
4. Fan out to source adapters (LinkedIn, Indeed, Glassdoor, Greenhouse,
   Lever, Ashby, company RSS).
5. Normalize → JobPosting schema.
6. Dedupe (title+company+location near-match + URL canonicalization).
7. Relevance: semantic similarity to user skill-graph; ghost-job heuristics
   (posting age > 60d, repost frequency, vague JD).
8. Enrich with company id, stack hints, headcount.
9. Self-critique: precision proxy (relevance ≥ 0.6 share), recall proxy
   (sources covered).
10. Emit ranked DiscoveryBatch.
```

### 2.3 Company Vetting & Research Agent

```
1. For each shortlisted company:
     - pull Glassdoor rating, Crunchbase funding, news sentiment (NewsAPI),
       layoff records (layoffs.fyi), tech stack (StackShare/BuiltWith),
       DEI signals (public reports), Glassdoor interview difficulty.
2. Reason about risk/reward: weights from user prefs (e.g. user weights
   stability 0.4, comp 0.3, culture 0.3).
3. Emit RiskRewardScorecard with explainable per-criterion reasoning.
4. Threshold gate: only companies ≥ user threshold proceed.
```

### 2.4 Resume & Cover Letter Architect Agent

This is the most safety-critical agent.

```
1. Parse JD → extract hard skills, soft skills, hidden requirements,
   ATS keyword set (TF-IDF + LLM extraction, intersected).
2. Map each requirement → best evidence from user skill-experience graph
   with justification. Reject any unsupported claim.
3. Plan resume sections + ordering for this JD.
4. Generate via controlled template engine + LLM in *fill-in-the-blanks*
   mode (templates own structure; LLM owns wording within bounded slots).
5. Self-review loop:
     - hallucination check: every bullet must cite a graph node id
     - ATS check: keyword coverage ≥ target, no tables/images, proper
       headings, parseable by pyresparser-style parser
     - alignment check: each JD requirement has ≥ 1 mapped bullet
     - tone check: matches company culture vector
6. If any check fails → revise; max N revisions then escalate to human.
7. Render to PDF + DOCX (WeasyPrint + python-docx).
8. Peer critique: Vetting Agent validates against company culture.
9. Submit for human one-click approval.
```

### 2.5 Application & Submission Agent

```
1. Inspect target portal: detect Greenhouse/Lever/Workday/Ashby/custom.
2. Choose path: API (preferred) → headless auto-fill → email → manual
   one-click assist.
3. Dry-run: build the submission payload, verify required fields, surface
   any unknowns to the user.
4. On submit: capture confirmation id, screenshot, response payload.
5. Post-submission Reflection: which path worked, which fields failed,
   what to memoize for next time.
```

### 2.6 Progress Tracking & Communication Agent

```
1. IMAP/Gmail watcher + Calendar API.
2. Classify inbound mail: rejection / interview invite / assessment /
   recruiter outreach / generic.
3. Resolve to ApplicationId (subject heuristics + company match +
   thread tracking).
4. Move card on Kanban: Saved → Applied → Assessment → Interview →
   Offer → Closed.
5. Schedule prep packs (company brief, JD recap, likely questions) on
   the calendar event.
```

### 2.7 Continuous Improvement Meta-Agent

```
1. Stream user actions (edits to resumes, rejections of jobs, manual
   pipeline moves, dashboard clicks).
2. Infer hidden preferences (e.g., user consistently rejects jobs > 30
   miles → tighten geo filter).
3. Update agent prompts / weights / thresholds with rollback on regression.
4. A/B test prompt changes against historical traces.
```

---

## 3. Tech stack and justifications

| Layer | Choice | Why |
|---|---|---|
| Agent framework | **LangGraph** | Typed state graphs, deterministic replay, fits plan-and-execute. CrewAI / AutoGen acceptable alternatives; LangGraph wins on observability. |
| LLM | Claude (Opus 4.7 for planning / critique, Sonnet 4.6 for high-volume tasks, Haiku 4.5 for cheap classification) with prompt caching | Best reasoning per dollar at the planner tier; Sonnet for throughput; Haiku for email classification. |
| API | **FastAPI** | Async-native, typed, OpenAPI for the dashboard client. |
| Queue | **Celery** (Python) or **BullMQ** if Node services are added | Mature, retry / backoff / dead-letter built in. |
| DB | **PostgreSQL** + **pgvector** | Single store for relational state and embeddings; cuts ops surface. |
| Cache / bus | **Redis** | Task broker + pub/sub for the thought stream. |
| Object store | **S3** (or MinIO local) | Resumes, cover letters, screenshots, raw scrape payloads. |
| Secrets | **HashiCorp Vault** or **AWS KMS** | Token vault for IMAP/OAuth/job-board cookies. |
| Headless scraping | **Playwright** | Reliable, anti-bot ergonomics, screenshotting. |
| Document gen | **WeasyPrint** + **python-docx** | Deterministic PDF; editable DOCX. |
| Frontend | **Next.js + React + Tailwind**, **shadcn/ui** | Mobile-first, dark mode, accessible primitives. |
| Realtime | **WebSocket** via FastAPI; SSE fallback | Thought stream + Kanban updates. |
| Observability | **OpenTelemetry → Tempo/Jaeger + Loki + Prometheus** | Trace across agents and tool calls. |
| Auth | **OAuth2** (Google + email/password) + JWT | Standard, plays well with Gmail/Calendar scopes. |
| Containers | **Docker Compose** (dev), **Kubernetes** (prod) | Each agent is an independent service. |
| Testing | **pytest**, **pytest-asyncio**, **hypothesis**, **playwright-pytest** | Unit + integration + agent-behavioral. |

---

## 4. Data model (essentials)

```
User(id, email, prefs_json, skill_graph_id, created_at)
SkillGraph(id, user_id, nodes_json)   -- experiences, skills, projects, evidence
JobHuntPlan(id, user_id, milestones_json, queries_json, status, version)
JobPosting(id, source, source_id, url, title, company_id, location,
           salary_band, posted_at, jd_text, jd_embedding, fingerprint,
           relevance_score, ghost_score)
Company(id, name, domain, glassdoor_id, crunchbase_id, scorecard_json)
Application(id, user_id, job_id, status, submitted_at, confirmation_id)
Document(id, application_id, kind, s3_key, content_hash, approved_by)
ReasoningTrace(id, agent, task_id, parent_trace_id, payload_jsonb,
               created_at)  -- append-only, partitioned by day
EmailEvent(id, user_id, message_id, classified_as, application_id)
UserAction(id, user_id, kind, payload_json, created_at)  -- for meta-agent
```

`ReasoningTrace` and `UserAction` are append-only with row-level
checksums; nightly job mirrors them to S3 as Parquet for the meta-agent.

---

## 5. Cross-cutting requirements

* **Resilience** — every external tool call goes through a wrapper that
  enforces: timeout, exponential-backoff retry, circuit breaker (per
  source), and a typed fallback (`degraded=True` flag on the result).
  When LinkedIn is down, discovery continues with the other sources and
  the trace records the degradation.
* **Auditability** — every agent decision writes one `ReasoningTrace`
  row; user-visible artifacts (resumes, submissions) carry the trace id
  in their metadata.
* **Security & privacy** — OAuth tokens in Vault; PII redaction filter
  on every log sink; per-user S3 prefixes with KMS-per-tenant keys;
  GDPR export/delete endpoints.
* **PII redaction in logs** — a single `redact()` utility runs on all
  log emissions and trace payloads; pattern set covers email, phone,
  full names from the profile, addresses.
* **Testing** — three tiers:
  1. *Unit* — pure functions (dedupe, relevance, JD parsing).
  2. *Integration* — adapter contracts with recorded fixtures (VCR).
  3. *Agent-behavioral* — given a fixed seed and a frozen JD/profile,
     the agent must produce an output that satisfies a checklist
     (≥ 0.8 keyword coverage, no hallucinations, etc.). Coverage target
     ≥ 80% lines, ≥ 90% on safety-critical modules (resume agent).

---

## 6. Implementation roadmap

### Phase 0 — Foundation (this PR)
* `jobhunt/` package: typed models, reasoning trace, agent base class,
  tool wrapper with retry/circuit-breaker, in-memory event bus.
* Orchestrator with a static plan-and-execute loop.
* Job Discovery agent with a pluggable adapter interface and one
  fixture-backed adapter (so tests run offline).
* Dedupe + relevance scoring + ghost-job heuristics.
* Stubs for the remaining agents with reasoning hooks so the
  Orchestrator graph compiles end-to-end.
* FastAPI dashboard skeleton: `/plan`, `/jobs`, `/traces`, WebSocket
  `/stream`, served alongside a minimal static HTML/JS client.
* CLI: `python -m jobhunt demo` runs a full plan→discover loop against
  fixtures and prints the reasoning stream.
* Pytest suite with > 80% coverage on the shipped modules.

### Phase 1 — Real sources
* Greenhouse, Lever, Ashby public-API adapters (no scraping needed).
* Indeed RSS adapter.
* Postgres + pgvector persistence.
* Celery + Redis task queue.

### Phase 2 — Resume Architect
* Skill-graph editor on the dashboard.
* JD parser + ATS keyword extractor.
* Templated resume engine + LLM in slot-fill mode.
* Self-critique + Vetting peer-critique.
* Human one-click approval UI.

### Phase 3 — Submission + Tracking
* Greenhouse/Lever auto-apply.
* Playwright auto-fill with one-click assist fallback.
* Gmail/Calendar integration.
* Kanban realtime updates.

### Phase 4 — Vetting + Meta-agent
* Company data enrichment pipeline.
* RiskRewardScorecard with weights from prefs.
* Meta-agent loop: action log → weight updates → A/B test.

### Phase 5 — Hardening
* OpenTelemetry across services.
* Vault integration.
* k8s manifests, autoscaling, SLOs.
* Penetration test + GDPR audit.

---

## 7. What's in this repo today

The `jobhunt/` package implements Phase 0. Run:

```
pip install -r requirements.txt
python -m jobhunt demo
```

Then start the dashboard:

```
python -m jobhunt serve   # http://localhost:8765
```

See `jobhunt/README` section in this file's repo for module-level
documentation.
