# Job link, per-job résumé editing, job details — design

Approved 2026-10-06 (Option A: derived, zero-cost details).

## 1. Job link
- Preview: "Open posting ↗" button + source host (e.g. `boards.greenhouse.io/capco`).
- Card: small ↗ link (stops click propagation). Frontend only.

## 2. Per-job résumé editor
- Preview "Edit" toggle → form over `doc.draft`: summary, entry headers
  (left/right), bullets (edit/add/remove), skills lines.
- `PUT /api/documents/{job_id}` `{draft}`: validate (strings, size caps),
  rebuild `resume_text` via `ResumeDraft.from_dict(draft).to_text()`, set
  `doc.edited = true`, persist. Downloads render from the stored draft.
- AI rewrite (`_start_polish`) never overwrites an edited document.
- Profile untouched: edits are per job.

## 3. Job details (no scraping, no tokens)
- Salary: posting band (`salary_min/max`) if present, else Adzuna market
  estimate, cached per (role, city) for the process lifetime.
- Company tier: curated map in code (Big Tech · Product unicorn · Mid-size
  product · Startup · Services/consulting); unknown → "unrated".
- Contacts: LinkedIn people-search deep links (recruiters; data engineering
  managers) at the company, India geo. Hunter emails when `HUNTER_API_KEY` set
  (existing integration). No LinkedIn scraping (ToS, bans).
- Shown on card (tier, salary) and in preview (all three).

## Testing
- API test for document edit (validation, text rebuild, polish skip).
- Unit tests for tier lookup, salary choice + cache, contact links.
