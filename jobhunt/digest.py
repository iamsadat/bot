"""Activity digest — a plain-text summary of pipeline movement.

``build_digest`` renders a readable daily/weekly recap (new matches, pipeline
counts by status, applications this week, and the current streak) reusing the
Phase-0 :func:`jobhunt.metrics.compute_funnel`. It is pure (no I/O); the server
endpoint + background loop are responsible for actually sending it via the
configured notifier sinks.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import date

from jobhunt.metrics import compute_funnel

# Canonical pipeline order for a stable, readable digest body. Kept local (not
# imported from server.py) so this module has no dependency back on the server.
_STATUS_ORDER = ("Saved", "Applied", "Assessment", "Interview", "Offer", "Closed")


def build_digest(state, period: str = "daily") -> dict:
    """Build ``{"subject", "body"}`` summarizing recent activity in ``state``.

    ``period`` controls the "new matches" lookback window: ``"weekly"`` looks
    back 7 days, anything else (the ``"daily"`` default) looks back 24 hours.
    """
    window = 7 * 86400 if period == "weekly" else 86400
    now = time.time()
    new_matches = sum(
        1 for j in state.jobs
        if any(
            e.get("stage") == "Discovered" and now - float(e.get("ts", 0.0)) <= window
            for e in j.get("events", [])
        )
    )

    counts = Counter(j.get("status", "Saved") for j in state.jobs)
    funnel = compute_funnel(state)

    pipeline_lines = "\n".join(f"  {s}: {counts.get(s, 0)}" for s in _STATUS_ORDER)
    subject = f"JobHunt {period} digest — {date.today().isoformat()}"
    body = (
        f"{new_matches} new match(es) since last digest.\n\n"
        f"Pipeline:\n{pipeline_lines}\n\n"
        f"Applied this week: {funnel['applied_this_week']}\n"
        f"Current streak: {funnel['streak']} day(s)"
    )
    return {"subject": subject, "body": body}
