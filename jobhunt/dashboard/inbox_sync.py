"""Wire the IMAP inbox + classifier into live application status updates.

Reads IMAP credentials from the environment, polls for new recruiter mail,
classifies each message (interview / assessment / offer / rejection), matches it
to a discovered job by company, and advances that job's status with a timeline
event and any Calendly/Zoom interview time. No-ops cleanly when IMAP isn't
configured, so it's safe to import and call unconditionally.

Credentials live in env (never persisted to the workspace DB):
  JOBHUNT_IMAP_HOST, JOBHUNT_IMAP_USER, JOBHUNT_IMAP_PASSWORD,
  JOBHUNT_IMAP_MAILBOX (default INBOX), JOBHUNT_IMAP_POLL_SECONDS (default 300).
"""

from __future__ import annotations

import os

from jobhunt.inbox.calendar import extract_calendar
from jobhunt.inbox.classify import classify_message

# Recruiter-email category -> pipeline status it should advance the job to.
_STATUS_FROM_LABEL = {
    "rejection": "Closed",
    "assessment": "Assessment",
    "interview": "Interview",
    "offer": "Offer",
}
_RANK = {"Saved": 0, "Applied": 1, "Assessment": 2, "Interview": 3, "Offer": 4, "Closed": 5}


def build_inbox_from_env():
    """Return an ``IMAPInboxSource`` from env vars, or ``None`` if unconfigured."""
    host = os.environ.get("JOBHUNT_IMAP_HOST")
    user = os.environ.get("JOBHUNT_IMAP_USER")
    pw = os.environ.get("JOBHUNT_IMAP_PASSWORD")
    if not (host and user and pw):
        return None
    from jobhunt.inbox.sources import IMAPInboxSource
    return IMAPInboxSource(
        host=host, username=user, password=pw,
        mailbox=os.environ.get("JOBHUNT_IMAP_MAILBOX", "INBOX"),
    )


def _match_job(jobs: list, message) -> dict | None:
    """Match a message to a job by company name or sender domain."""
    comp = (getattr(message, "company", "") or "").lower().replace(" ", "")
    sender = (getattr(message, "sender", "") or "").lower()
    for j in jobs:
        jc = (j.get("company", "") or "").lower().replace(" ", "")
        if not jc:
            continue
        if comp and (jc in comp or comp in jc):
            return j
        if jc in sender:
            return j
    return None


def sync_inbox(state, source, *, since: float = 0.0, max_messages: int = 50) -> dict:
    """Fetch + classify mail and advance matched jobs. Returns a summary dict."""
    from jobhunt.dashboard.server import _add_event  # local import avoids a cycle

    try:
        messages = source.fetch(since=since, max_messages=max_messages)
    except Exception as exc:  # IMAP/network failure must not crash the caller
        return {"ok": False, "error": str(exc), "checked": 0, "updates": 0}

    updates = 0
    for m in messages:
        cls = classify_message(m.subject, m.body)
        target = _STATUS_FROM_LABEL.get(cls.label)
        if target is None:
            continue
        job = _match_job(state.jobs, m)
        if job is None:
            continue
        old = job.get("status", "Saved")
        # Only advance forward; a rejection is terminal and always wins.
        if target != "Closed" and _RANK.get(target, 0) <= _RANK.get(old, 0):
            continue

        job["status"] = target
        detail = f"Email: {m.subject[:70]}"
        cal = extract_calendar(m.body)
        if cal.proposed_time:
            job["next_action"] = f"Interview: {cal.proposed_time}"
            detail += f" · {cal.proposed_time}"
        elif cal.has_link and getattr(cal, "link", ""):
            job["next_action"] = f"Schedule: {cal.link}"
        _add_event(state, job["job_id"], target, detail)
        state.bus.publish(
            "inbox", job["job_id"],
            f"{job.get('company', '')}: {cls.label} email → {target}",
        )
        updates += 1

    if updates:
        state.persist()
    return {"ok": True, "checked": len(messages), "updates": updates}
