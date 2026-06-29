"""Tests for fully-autonomous capped auto-apply."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("fastapi")

from jobhunt.dashboard.server import (  # noqa: E402
    DashboardState, _maybe_auto_apply_batch,
)
from jobhunt.models import UserProfile  # noqa: E402
from jobhunt.submitters.base import FakePoster  # noqa: E402
from jobhunt.submitters.greenhouse import GreenhouseSubmitter  # noqa: E402
from jobhunt.submitters.lever import LeverSubmitter  # noqa: E402
from jobhunt.submitters.registry import SubmitterRegistry  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _registry(poster):
    return SubmitterRegistry([GreenhouseSubmitter(poster), LeverSubmitter(poster)])


def _seed(st, jid, score=1.0):
    url = f"https://boards.greenhouse.io/acme/jobs/{jid}"
    st.jobs.append({"job_id": jid, "company": "Acme", "title": "Backend",
                    "location": "Remote", "url": url, "status": "Saved",
                    "relevance_score": score, "events": []})
    st.documents[jid] = {
        "job_id": jid, "company": "Acme", "title": "Backend", "url": url,
        "resume_text": "Ada\nada@x.com", "cover_letter_text": "Dear",
        "keyword_coverage": 0.8, "matched_keywords": [], "missing_keywords": [],
        "bullets": [], "draft": None,
    }
    st.approval_queue.submit(job_id=jid, document_id=f"d{jid}",
                             company="Acme", title="Backend")


def _state(auto=True, cap=5, floor=0.0):
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    st.user_profile = UserProfile(
        user_id="u", name="Ada", email="ada@x.com", target_roles=[], locations=[],
        auto_apply=auto, daily_apply_cap=cap, relevance_floor=floor)
    st.ats_config = {"greenhouse_tokens": ["acme"], "lever_slugs": [], "ashby_slugs": []}
    return st


def test_autonomous_apply_submits_pending_when_connected():
    poster = FakePoster()
    poster.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/1", 201, {"id": "GH-1"})
    st = _state()
    _seed(st, "1")
    n = _maybe_auto_apply_batch(st, _registry(poster))
    assert n == 1
    assert st.jobs[0]["submitted"] is True
    assert st.applies_today[date.today().isoformat()] == 1


def test_autonomous_apply_stops_at_daily_cap():
    poster = FakePoster()
    for jid in ("1", "2", "3"):
        poster.add(f"https://boards-api.greenhouse.io/v1/boards/acme/jobs/{jid}",
                   201, {"id": f"GH-{jid}"})
    st = _state(cap=2)
    for jid in ("1", "2", "3"):
        _seed(st, jid)
    n = _maybe_auto_apply_batch(st, _registry(poster))
    assert n == 2  # capped
    submitted = [j for j in st.jobs if j.get("submitted")]
    assert len(submitted) == 2
    # A second sweep the same day stays capped.
    assert _maybe_auto_apply_batch(st, _registry(poster)) == 0


def test_relevance_floor_skips_low_matches():
    poster = FakePoster()
    poster.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/1", 201, {"id": "GH-1"})
    st = _state(floor=0.5)
    _seed(st, "1", score=0.2)  # below floor
    assert _maybe_auto_apply_batch(st, _registry(poster)) == 0
    assert not st.jobs[0].get("submitted")
