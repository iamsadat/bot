"""Local query filters shared by all real adapters.

Greenhouse / Lever / Ashby public APIs return the *full* board — they
have no native search. We apply the same role / location / remote /
exclude rules client-side so the JobSource protocol stays consistent
with the in-memory FixtureSource.
"""

from __future__ import annotations

from jobhunt.models import JobPosting


def passes_local_filters(posting: JobPosting, query: dict) -> bool:
    role = (query.get("role") or "").lower()
    location = (query.get("location") or "").lower()
    remote_ok = query.get("remote_ok", True)
    excluded = {c.lower() for c in query.get("exclude_companies", [])}

    if posting.company.lower() in excluded:
        return False

    if role and role not in posting.title.lower():
        # Soft match: require ALL role tokens to appear in the JD so
        # "backend engineer" does not accidentally accept a Frontend
        # role just because the word "engineer" is present.
        jd_lc = posting.jd_text.lower()
        tokens = [t for t in role.split() if t]
        if not tokens or not all(t in jd_lc for t in tokens):
            return False

    if location:
        if location in posting.location.lower():
            return True
        if remote_ok and posting.remote:
            return True
        return False

    return True
