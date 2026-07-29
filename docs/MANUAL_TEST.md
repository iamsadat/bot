# Manual end-to-end test

How to drive the whole product yourself and see each stage work. No API keys
and no ATS accounts needed — the offline fixture path is the default.

## Setup

```bash
cd /path/to/bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

cd frontend && npm ci && npm run build && cd ..   # needed for the modern UI at /

python -m jobhunt serve --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>.

Without the `npm run build` step, `/` falls back to the older single-file SPA
(`jobhunt/dashboard/client.html`). That's a different UI — build the frontend
or you're testing the wrong thing.

## The journey

1. **Onboarding** — go to `/onboarding`.
   - Paste any résumé text and hit **Parse & prefill**. Skills, experience,
     education and projects should populate below.
   - **Enter several target roles.** This matters: the offline fixture set is
     six postings, and the role/location filter is a real filter. With only
     `backend engineer` + `Remote` you get **1** job, which looks broken but
     isn't. With `backend engineer, software engineer, backend developer` and
     locations `Remote, San Francisco CA, New York NY` you get **3**.
   - Optionally fill **Connect job boards** (Greenhouse token / Lever slug /
     Ashby slug). Leave it empty for the offline run — see step 5 for what
     changes when it's set.
   - Hit **Save profile**.

2. **Run the hunt** — go to `/dashboard`, hit **Run hunt**. It finishes in
   well under a second on fixtures. Cards appear on the Kanban with a match
   percentage. If the hunt fails you now get a warning banner with the error
   instead of a silent empty board.

3. **Inspect a tailored résumé** — click a card. The drawer shows the
   generated résumé, keyword coverage, and matched/missing keywords. Every
   bullet is evidence-backed, so with no LLM key set you still get real
   output — the LLM only polishes wording when a key is present.

4. **Download** — all four buttons (PDF / DOCX / HTML / TXT) should download a
   real file. PDF opens as a 1-page PDF, DOCX opens in Word. If a renderer is
   missing you get an inline message rather than the browser navigating to a
   raw JSON error.

5. **Approve** — hit **Approve & apply**, or open the approvals panel by
   clicking the *Pending approval* stat card on the dashboard.
   - With **no board connected**: the job is marked Applied and you finish on
     the company site. The response says `{"submitted": false, "manual": true}`.
     This is deliberate — fixture jobs use real-looking Greenhouse URLs, so
     submitting them would fire junk at a real ATS.
   - With a **board connected**: approving posts a real application through
     the Greenhouse/Lever API and records a confirmation id.

6. **Track** — the drawer has a status selector (Saved → Applied →
   Assessment → Interview → Offer → Closed). Changing it moves the card
   between Kanban columns and appends a timeline event.

7. **Other surfaces** — `/insights` (funnel, A/B, skill gaps, CRM),
   `/interview` (question generation + answer scoring), `/tools/ats-score`
   (public, no auth), `/admin` (waitlist + traffic; needs
   `JOBHUNT_ADMIN_TOKEN` set before starting the server).

## Known limits, so they don't read as bugs

- **Six fixture postings.** Narrow role/location filters legitimately reduce
  that to 1–3 results.
- **Connecting a real board makes discovery hit the network.** On a machine
  without outbound access to `boards.greenhouse.io` the hunt will block on
  those requests. Leave boards empty to stay fully offline.
- **Free-tier deploys lose per-workspace data.** Multi-tenant workspaces sit
  on ephemeral disk; a redeploy or idle spin-down resets them. Set
  `DATABASE_URL` for the shared stores (public résumés, auth, waitlist,
  pageviews) to survive.
- **Playwright autofill is optional** and skipped unless installed.
