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
   - Upload a PDF or DOCX résumé, or paste the text. Skills, experience,
     education and projects populate below, and **target roles are prefilled**
     from what the résumé says you do — plus adjacent roles your skills
     support, which you can delete.
   - **Enter your locations, including your country.** A board that posts
     "Bengaluru" is matched to "India", but only if India is on your list.
   - **Connect job boards is optional.** Leave it empty and the hunt searches
     177 curated public company boards. Add handles to search a specific
     employer as well.
   - Hit **Save profile**.

2. **Run the hunt** — go to `/dashboard`, hit **Run hunt**. Against live
   sources it takes about ten seconds. Cards appear on the Kanban with a match
   percentage. If the hunt fails you get a warning banner with the error
   instead of a silent empty board.

3. **Check a match makes sense** — click a card. The drawer opens with **Why
   NN% match**: role match, the skills the posting asked for, level fit and
   location fit, with the technologies you have in green and the ones you lack
   in grey. This is the fastest way to tell a bad result from a bad score.

4. **Inspect the tailored résumé and download it** — the same drawer shows the
   generated résumé and keyword coverage. Every bullet is evidence-backed, so
   with no LLM key set you still get real output; the LLM only polishes wording
   when a key is present. All four download buttons (PDF / DOCX / HTML / TXT)
   should produce a real file.

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

- **A new workspace searches curated public company boards.** 177 verified
  Greenhouse / Lever / Ashby boards, weighted towards India, plus the keyless
  feeds below. No credentials of any kind. Boards are read six at a time
  (`JOBHUNT_BOARDS_PER_PAGE`; personal mode reads all of them, ~45s), so
  repeat "Fetch more" clicks reach new companies; the source panel says which
  page you are on. Connect your own handles on `/onboarding#boards` to search
  specific employers as well.
- **Auto-apply is available but off.** Seeded boards are real Greenhouse and
  Lever boards, so the submitters can post to them and the toggle is no longer
  greyed out. Nothing is submitted until you turn auto-apply on and set a daily
  cap — both default to off/zero.
- **Keyless feeds**: Arbeitnow (paginated, Europe-heavy), Himalayas (paginated,
  remote-only, publishes seniority and country restrictions) and Remote OK
  (last, because its board carries a lot of junk).
- **Adzuna is the only source with real non-remote coverage outside the
  US/EU remote market.** Set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` (free at
  developer.adzuna.com). The country is taken from your profile's locations,
  so a Hyderabad profile searches India; `ADZUNA_COUNTRY` overrides it.
- **Match scores are explainable.** Click a job and the drawer shows role
  match, skill coverage, level fit and location fit, plus which of the
  posting's technologies you have. A score marked "not counted" for skills
  means the description named too few technologies to judge from.
- **Results are filtered on the title, not the description.** Searching "data
  engineer" will not return "Data Analyst" — different job family — and will
  not return roles more than one level above you. If a board looks empty, add
  more target roles rather than assuming discovery is broken.
- **Connected ATS boards have no page 2.** Greenhouse/Lever/Ashby hand back
  their entire board in one request, so once you've fetched, nothing new
  appears until that company posts a role.
- **Fully offline runs**: set `JOBHUNT_OFFLINE=1` to skip the network sources
  and use the six-row fixture set instead. The test suite pins this
  automatically, and it also keeps the submission gate shut.
- **GitHub import is rate-limited** to 60 requests/hour per IP when
  unauthenticated. Set `GITHUB_TOKEN` (a classic token, no scopes) to raise it
  to 5000/hour. The error message tells you which limit you hit.
- **Free-tier deploys lose per-workspace data.** Multi-tenant workspaces sit
  on ephemeral disk; a redeploy or idle spin-down resets them. Set
  `DATABASE_URL` for the shared stores (public résumés, auth, waitlist,
  pageviews) to survive.
- **Playwright autofill is optional** and skipped unless installed.

## Re-checking the seeded boards

Company boards get retired. `jobhunt/company_boards.py` lists each slug with
the posting counts measured when it was added; a dead slug is now survivable
(the adapter skips it and carries on) but wastes a slot. To re-verify, fetch
`https://boards-api.greenhouse.io/v1/boards/<slug>/jobs` — a 404 means the slug
is gone.
