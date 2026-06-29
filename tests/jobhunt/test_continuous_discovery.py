"""Tests for continuous discovery (merge, never clear) + autonomy gating."""

from __future__ import annotations

from jobhunt.dashboard.server import (
    DashboardState, _maybe_auto_apply_batch, _merge_discovered,
)
from jobhunt.models import JobPosting, UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


def _state():
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def _posting(jid, company="Acme", title="Backend"):
    return JobPosting(
        job_id=jid, source="greenhouse", source_id=jid,
        url=f"https://boards.greenhouse.io/{company.lower()}/jobs/{jid}",
        title=title, company=company, location="Remote",
        jd_text="Python backend.",
    )


def test_merge_adds_new_and_dedupes_by_fingerprint():
    st = _state()
    added = _merge_discovered(st, [_posting("1"), _posting("2", company="Globex")])
    assert added == 2
    assert len(st.jobs) == 2
    # Same company/title/location → same fingerprint → not re-added.
    again = _merge_discovered(st, [_posting("1"), _posting("3", title="Frontend")])
    assert again == 1
    assert len(st.jobs) == 3


def test_merge_never_clears_existing_jobs():
    st = _state()
    st.jobs = [{"job_id": "x", "company": "Existing", "title": "T",
                "location": "L", "status": "Applied", "events": []}]
    _merge_discovered(st, [_posting("1")])
    assert any(j["job_id"] == "x" for j in st.jobs)
    assert len(st.jobs) == 2


class _FakeReg:
    """Registry stub: matches greenhouse URLs, records submissions."""

    def __init__(self):
        self.calls = []

    def for_url(self, url):
        return object() if "greenhouse.io" in (url or "") else None


def test_autonomy_off_does_not_apply():
    st = _state()
    st.user_profile = UserProfile(user_id="u", name="A", email="a@x.com",
                                  target_roles=[], locations=[], auto_apply=False)
    st.ats_config = {"greenhouse_tokens": ["acme"]}
    n = _maybe_auto_apply_batch(st, _FakeReg())
    assert n == 0


def test_autonomy_requires_connected_ats():
    st = _state()
    st.user_profile = UserProfile(user_id="u", name="A", email="a@x.com",
                                  target_roles=[], locations=[], auto_apply=True,
                                  daily_apply_cap=5)
    # No ats_config → not connected → no auto-apply even though flag is on.
    assert _maybe_auto_apply_batch(st, _FakeReg()) == 0
