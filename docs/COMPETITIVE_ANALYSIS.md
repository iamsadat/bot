# Competitive Analysis: JobHunt vs. LoopCV vs. JobCopilot

_Last updated: 2026-06-28. Competitor facts are sourced inline; where a number
could not be verified from a primary source it is flagged as **unverified**.
Pricing on these tools changes often and varies by region/billing cycle — treat
the figures as "as observed mid-2026," not gospel._

---

## 1. Honest verdict (one paragraph)

LoopCV and JobCopilot are mature, hosted, paid products that win today on
**breadth and "it just runs"**: they apply at real volume (tens of applications
a day) across hundreds of thousands of company career pages and major boards,
ship browser extensions, recruiter-email outreach, interview practice, and
hands-off auto-submit — none of which JobHunt fully delivers yet. But their
core weakness is the same one users complain about loudest: **quality**. Both
lean on volume, both produce generic, boilerplate cover letters and screening
answers, both surface ghost/irrelevant listings, and JobCopilot openly admits a
sub-2% callback rate. JobHunt is **earlier and narrower** (real auto-submit only
for Greenhouse/Lever, discovery-only on LinkedIn/Indeed, no hosted offering) but
is architecturally aimed exactly at the gap the incumbents leave open:
**evidence-backed, non-hallucinated tailoring, transparent multi-agent
reasoning, self-hostable privacy, and a $0 free-tier LLM.** JobHunt cannot beat
them on volume this quarter; it can beat them on **trust, transparency, and
cost** now, and close the volume gap with a focused roadmap.

---

## 2. Feature comparison matrix

Legend: **Yes** / **Partial** / **No**. Notes are honest, not marketing.

| Capability | LoopCV | JobCopilot | JobHunt |
|---|---|---|---|
| Hosted / zero-setup SaaS | **Yes** | **Yes** | **No** — self-host only (Docker/Render one-click), no managed offering |
| Open-source / self-hostable | **No** | **No** | **Yes** — full source, run your own instance, your data stays local |
| Auto-apply at volume (10s/day) | **Yes** — up to ~300/mo on top tier | **Yes** — 20/day (Premium), 50/day (Elite) | **No** — submits one-at-a-time on human approve |
| Real submit to ATS career pages | **Partial** — autofill + recruiter-email outreach (CV often *not* attached) ¹ | **Yes** — claims official channels across 500k+ career pages ² | **Partial** — real POST to **Greenhouse + Lever** APIs only; others = "marked Applied, finish on site" |
| LinkedIn apply | **Yes** — Easy Apply + external (Chrome ext.) ³ | **Partial** — autofill ext. on any site | **No** — LinkedIn appears only as a discovery query string, no adapter or apply |
| Indeed apply | **Partial** (board coverage) | **Partial** (autofill) | **No** — Indeed is RSS **discovery** only (`adapters/indeed.py`) |
| Greenhouse / Lever / Ashby | **Partial** (listed among 30+ boards) | **Partial** (career-page crawl) | **Yes (discovery)**; **submit: Greenhouse + Lever**, Ashby discovery-only |
| Browser-extension autofill | **Yes** (reported flaky) ⁴ | **Yes** — "One-Click Autofill on any site" | **No** — autofill modules for Workday/iCIMS exist as **scaffolding** (`autofill/`), not a shipped extension |
| ATS custom-question answering | **Partial** (templated) | **Partial** (generic, complaint source) | **No** — not yet handled by the submitter |
| Resume tailoring per job | **Partial** (CV matching) | **Yes** — per-application tailoring (Elite) | **Yes** — JD parser + slot-fill template, ATS keyword coverage scoring |
| AI cover letters | **Yes** (generic, complaint source) | **Yes** (generic, complaint source) | **Yes** — evidence-bound; optional LLM tone-polish |
| **Evidence-backed / no hallucinated experience** | **No** | **No** | **Yes** — every bullet needs an `evidence_id`; draft is rejected otherwise |
| Transparent reasoning / audit trail | **No** | **No** | **Yes** — `deliberate→act→critique→decide` ReasoningTrace per agent, streamed live |
| Company vetting / risk scoring | **No** | **Partial** (basic) | **Yes** — Glassdoor/Crunchbase/News/Layoffs enrichers + weighted scorecard |
| Application tracker / Kanban | **Yes** | **Yes** | **Yes** — Kanban + per-application timeline |
| Recruiter-email auto-status | **Partial** (outreach, not inbound status) | **Partial** (hiring-manager emails) | **Partial** — IMAP watcher + confidence classifier built; **not yet wired live** to status |
| Recruiter-email outreach | **Yes** (email finder) | **Yes** (contact credits) | **No** |
| Interview practice / mock | **No** | **Yes** — roleplay chatbot | **No** |
| Free tier | **Yes** — 10 apps/mo | **No** (trial only) | **Yes** — full app, free; **$0 LLM** via Gemini free tier |
| Deterministic / offline test suite | n/a (closed) | n/a (closed) | **Yes** — ~180 tests, no network, every dependency has a `Fake*` |
| Human-approval gate | optional | optional | **Yes** — default; never auto-fires without opt-in |

¹ jobcopilot.com/loopcv-best-alternative — LoopCV described as message-first,
   "your CV isn't attached when these go out."
² jobcopilot.com — "Works Across 500,000+ Company Career Pages."
³ loopcv.pro/linkedin-auto-apply.
⁴ resumejudge.com and adzuna.com reviews report the Chrome extension as
   unreliable / "non-functional" for some users.

---

## 3. Pricing & volume comparison

> Competitor pricing varies by source and billing cycle. Numbers below are the
> most consistently reported mid-2026 figures, with conflicts flagged.

### LoopCV
| Tier | Price | Volume | Notes |
|---|---|---|---|
| Basic Looper | Free | 10 applications/mo, 1 job title, 3 boards | "Free forever," no card |
| Standard Looper | ~$19.99 / €19.99 /mo | 100 apps/mo, ~20 boards | Daily auto-apply + recruiter email |
| Premium Looper | ~$59.99 /mo | 300 apps/mo, 50 job titles | Mass-apply, advanced filters, priority support |
| Done For You | ~$89.99 /mo | Premium + advisory | Weekly calls, ATS improvements |

> **Pricing conflict (unverified):** some sources cite an entry paid tier at
> **€9.99/mo**; resumejudge/others cite **$19.99** as the entry paid tier.
> Limits are **monthly**, not daily. Credits **do not roll over** (complaint).
> Source: loopcv.pro/pricing, resumejudge.com/blog/loopcv-review, capterra.

### JobCopilot
| Tier | Price | Volume | Notes |
|---|---|---|---|
| Premium | ~$19.9–27.9 /mo | Up to **20 applications/day**, 1 copilot | Auto-apply, AI resume + cover letter, mock interviews, tracker |
| Elite | ~$24.9–31.5 /mo | Up to **50 applications/day**, 3 copilots | + per-application resume tailoring, hiring-manager contact credits |

> Weekly / monthly / quarterly billing; quarterly ~40% cheaper. Monthly price
> varies by cycle across sources (hence the ranges). Bot runs a ~4-hour search
> cycle. Source: jobcopilot.com/pricing, usesprout.com, wobo.ai, jobsolv.com.

### JobHunt
| Tier | Price | Volume | Notes |
|---|---|---|---|
| Self-host | **$0 software** | Bounded by ATS rate limits + human approve | Only cost is your own hosting (free Render tier works) |
| LLM | **$0** (Gemini free tier) or paid Anthropic | — | Heuristic fallback means it runs with **no** LLM key at all |

**Takeaway:** JobHunt is the only $0-software, $0-LLM, privacy-preserving
option. The incumbents charge ~$20–90/mo and meter volume; JobHunt's "volume"
is currently gated by its human-approval gate and limited submit coverage, not
by a paywall.

---

## 4. Where each competitor wins (today)

**LoopCV wins on:**
- Breadth of boards (30+ incl. LinkedIn, Indeed, Glassdoor, Workday, Greenhouse).
- LinkedIn Easy-Apply + external auto-apply via extension.
- Recruiter **email finder + outreach** (a channel JobHunt lacks entirely).
- A genuine free tier and a long track record / large user base.
- Praised human support.

**JobCopilot wins on:**
- Sheer hands-off volume — **20–50 real applications/day**.
- Claimed reach across **500k+ company career pages** via official channels.
- A bundled suite: AI resume builder, cover letters, **mock-interview roleplay**,
  per-application tailoring, hiring-manager contact credits.
- One-click autofill extension that works on arbitrary sites.

**Both win on:** being hosted/zero-setup, mature onboarding, and "set it and
forget it" automation that simply runs without you operating a server.

---

## 5. Where JobHunt already wins (or can uniquely win)

These are real, defensible advantages — and they target the incumbents' single
biggest, repeatedly-documented weakness: **generic, low-trust, low-callback
applications.**

1. **Evidence-backed tailoring (anti-hallucination).** JobHunt refuses to ship a
   résumé bullet without an `evidence_id` from the user's experience graph
   (`resume_template.build_resume_draft` + `ResumeArchitectAgent.critique`).
   Neither competitor makes this guarantee; both are documented producing
   boilerplate. **This is the headline differentiator.**
2. **Transparent multi-agent reasoning.** Every decision emits an inspectable
   ReasoningTrace streamed live to the dashboard. The incumbents are black boxes.
3. **Privacy / self-host.** Your résumé, inbox, and tokens never leave your
   instance. No double-billing, no "charged after cancellation," no scam-job
   exposure from a shared crawler — all real complaints against the incumbents.
4. **$0 cost.** Free software + Gemini free-tier LLM (or fully heuristic with no
   key). Competitors are $20–90/mo with non-rolling credits.
5. **Quality-gated, not volume-gated.** Human-approval default + per-criterion
   vetting scorecard aim at callback rate, the metric JobCopilot admits is <2%.
6. **Verifiable engineering.** ~180 deterministic offline tests; circuit
   breakers; graceful degradation. Trust as a product feature.

---

## 6. Plan to get better than them (prioritized roadmap)

Ordered by **impact ÷ effort**. Items already in-flight are flagged
**[in-flight]**. Each maps to concrete code.

### Tier 1 — Close the "it actually applies" gap (highest impact)

1. **Real PDF + ATS custom-question submit** **[in-flight]** — *High impact, High effort.*
   The Greenhouse submitter currently sends `resume_text` as the PDF bytes and
   ignores custom questions (`submitters/greenhouse.py`). Attach the real
   rendered PDF from `resume_renderer.py`, and add a question-answering step
   (map Greenhouse/Lever `questions` payloads → profile + LLM, with the same
   evidence/no-hallucination guard). **This is the single biggest blocker to
   parity** — without it, "auto-apply" is a half-promise.

2. **Ship the autofill browser path for Workday/iCIMS/generic** — *High impact, High effort.*
   The scaffolding exists (`autofill/workday.py`, `icims.py`, `generic.py`,
   `mapper.py`) but isn't driven by a real headless browser or extension. Wire
   Playwright (already the planned dependency in ARCHITECTURE §3) behind the
   existing `AutofillResult` interface to turn dry-run field maps into real
   submissions. This is how the incumbents cover the long tail of career pages.

3. **Recruiter-email auto-status, wired live** **[in-flight]** — *High impact, Medium effort.*
   The classifier (`inbox/classify.py`) and IMAP source exist and the
   TrackingAgent can move pipeline states (`agents/tracking.py`), but inbound
   classification isn't connected to the live application records. Close the loop:
   IMAP poll → classify → resolve to `application_id` (replace the placeholder
   `job_id` substring match) → auto-advance the Kanban card + timeline event.

### Tier 2 — Match their breadth

4. **Application tracker polish** **[in-flight]** — *Medium impact, Low effort.*
   Per-application timeline and Kanban already ship; add saved-search history and
   response-rate analytics so users can *see* JobHunt's quality edge as a number.

5. **Broaden real-submit coverage: Ashby apply + Workday via #2** — *Medium impact, Medium effort.*
   Ashby is discovery-only today; add an Ashby submitter alongside
   `submitters/greenhouse.py` / `lever.py` to push the "real API submit" board
   count from 2 → 3+.

6. **LinkedIn / Indeed apply path (carefully)** — *High impact, High effort, ToS-sensitive.*
   Currently discovery-only. ARCHITECTURE explicitly defers LinkedIn scraping in
   favor of an OAuth + human-assist path — keep that stance: offer a
   **one-click-assist** apply (pre-fill + user confirms) rather than silent
   scraping, which also dodges the account-risk complaints aimed at LoopCV's
   extension.

### Tier 3 — Differentiate harder (turn our edge into features)

7. **Surface "callback-rate" and "evidence coverage" as headline metrics** — *Medium impact, Low effort.*
   We already compute keyword coverage and track outcomes. Put a dashboard tile
   that contrasts "tailored + evidence-backed" vs. the incumbents' admitted <2%.
   Cheap, and it markets the core differentiator.

8. **Recruiter-outreach channel (opt-in, evidence-backed)** — *Medium impact, Medium effort.*
   LoopCV's email finder is a real gap for us. If we add it, do it the JobHunt
   way: attach the real CV (LoopCV's doesn't), and never fabricate claims.

9. **Hosted/managed offering** — *High impact (GTM), High effort.*
   No managed tier exists; `render.yaml` + Docker make a paid hosted version
   plausible. This is a business decision, not just engineering — the open-source
   self-host story is itself a differentiator and need not be abandoned.

### Explicitly behind, and honest about effort
- Mock-interview roleplay (JobCopilot has it) — **not started**, medium effort,
  low strategic priority versus Tier 1.
- Browser extension at scale — **not started**, high effort; gate behind #2.
- Horizontal scale / hardening (OpenTelemetry, Vault, k8s, GDPR audit) — Phase 5,
  **not started**; required before any hosted/paid offering (#9).

### Sequencing recommendation
Do **#1 → #3 → #4** first (all in-flight, all close the credibility gap with the
least new surface area), then **#2** to unlock the long tail of career pages,
then evaluate **#9** once Phase 5 hardening lands. Resist chasing raw volume:
JobHunt's win condition is *higher callback per application*, not more spam.

---

## Sources

- LoopCV: https://www.loopcv.pro/ · /pricing/ · /autoapply/ · /linkedin-auto-apply/
- LoopCV reviews: https://resumejudge.com/blog/loopcv-review/ ·
  https://www.adzuna.com/blog/loopcv-review-and-the-best-alternatives/ ·
  https://www.trustpilot.com/review/loopcv.pro · https://www.capterra.com/p/246545/Loopcv/
- JobCopilot: https://jobcopilot.com/ · /pricing/ · /loopcv-best-alternative/
- JobCopilot reviews: https://jobsolv.com/blog/jobcopilot-review-2025-legit-ai-tool-or-red-flag ·
  https://www.wobo.ai/blog/jobcopilot-review/ · https://www.usesprout.com/blog/jobcopilot-review-pricing-alternatives ·
  https://blog.theinterviewguys.com/job-copilot-review-2026/
- JobHunt: this repository (`README.md`, `ARCHITECTURE.md`, `jobhunt/`).
