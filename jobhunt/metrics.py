"""Funnel + outcome metrics (Phase-0 instrumentation).

A single pure function, :func:`compute_funnel`, turns the live dashboard
``state`` into the stage counts, conversion ratios, and streak figures the
Insights surface (and the digest) report on. It is deliberately side-effect
free and duck-typed on ``state`` so it works against any ``DashboardState``
(even pre-onboarding, where ``user_profile`` is ``None``).
"""

from __future__ import annotations

from datetime import date, timedelta

# Statuses that mean an application was actually submitted (Closed included —
# a closed application was still applied-to).
_APPLIED_STATUSES = {"Applied", "Assessment", "Interview", "Offer", "Closed"}


def _parse_days(days) -> set[date]:
    """Parse ISO date strings into a set of ``date`` objects, skipping junk."""
    out: set[date] = set()
    for d in days or []:
        try:
            out.add(date.fromisoformat(str(d)))
        except (ValueError, TypeError):
            continue
    return out


def compute_funnel(state) -> dict:
    """Compute the discovery→offer funnel + ratios + streak for ``state``."""
    jobs = state.jobs
    documents = state.documents

    discovered = len(jobs)
    tailored = len(documents)
    applied = sum(1 for j in jobs if j.get("status") in _APPLIED_STATUSES)
    interview = sum(
        1 for j in jobs
        if j.get("status") == "Interview"
        or any(e.get("stage") == "Interview" for e in j.get("events", []))
    )
    offer = sum(1 for j in jobs if j.get("status") == "Offer")
    callback_rate = round(interview / max(1, applied), 3)

    coverages = [float(d.get("keyword_coverage", 0.0) or 0.0) for d in documents.values()]
    evidence_coverage = round(sum(coverages) / len(coverages), 3) if coverages else 0.0

    # Streak + applied-this-week are derived from ``activity_days`` — the set of
    # days on which at least one application was actually submitted. Streak is
    # the run of consecutive days ending *today*; if today has no activity the
    # streak is 0. ``applied_this_week`` is the number of distinct active days
    # in the trailing 7-day window (today included).
    active = _parse_days(getattr(state, "activity_days", []))
    today = date.today()
    streak = 0
    cursor = today
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    week_start = today - timedelta(days=6)
    applied_this_week = sum(1 for d in active if week_start <= d <= today)

    profile = getattr(state, "user_profile", None)
    weekly_target = int(getattr(profile, "weekly_target", 10)) if profile else 10
    weekly_progress = round(applied_this_week / max(1, weekly_target), 3)

    return {
        "discovered": discovered,
        "tailored": tailored,
        "applied": applied,
        "interview": interview,
        "offer": offer,
        "callback_rate": callback_rate,
        "evidence_coverage": evidence_coverage,
        "applied_this_week": applied_this_week,
        "streak": streak,
        "weekly_target": weekly_target,
        "weekly_progress": weekly_progress,
    }
