"""Regression tests for the duplicate-approval bug.

A user reported *2 Discovered* but *15 Pending approval*: jobs deduped by
``(company, title, location)`` fingerprint while documents and approvals keyed
off ``job_id``, and ``FixtureSource`` minted a fresh uuid every ``search()``.
Every discovery sweep therefore queued another approval for the same role, and
``_maybe_auto_apply_batch`` — which resolves ``req.job_id`` against
``state.jobs`` — silently skipped every stale request, killing auto-apply.
"""

from __future__ import annotations

import pytest

from jobhunt.adapters import FixtureSource
from jobhunt.dashboard.server import (
    DashboardState, _discover_once, _execute_hunt, _github_username,
)
from jobhunt.models import UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u1", name="Ada", email="ada@x.com",
        target_roles=["backend engineer", "software engineer"],
        locations=["Remote", "New York, NY"],
        skills=["python", "kubernetes", "postgresql"],
    )


def _state() -> DashboardState:
    s = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    s.user_profile = _profile()
    return s


# ------------------------------------------------------------ stable job ids

def test_fixture_job_ids_are_stable_across_searches():
    src = FixtureSource(name="greenhouse", only_sources=["greenhouse"])
    query = {"role": "backend engineer", "location": "Remote"}
    first = [p.job_id for p in src.search(query)]
    second = [p.job_id for p in src.search(query)]
    assert first, "fixture should return at least one posting"
    assert first == second


def test_fixture_job_id_derives_from_source_and_source_id():
    src = FixtureSource(name="greenhouse", only_sources=["greenhouse"])
    posting = src.search({"role": "backend engineer", "location": "Remote"})[0]
    assert posting.job_id == f"{posting.source}:{posting.source_id}"


# ---------------------------------------------------- no duplicate approvals

def test_repeated_discovery_does_not_duplicate_approvals():
    """The actual reported bug: approvals grew by a constant on every sweep."""
    state = _state()
    _execute_hunt(state, None)
    baseline_jobs = len(state.jobs)
    baseline_approvals = len(state.approval_queue.all())
    assert baseline_jobs > 0

    for _ in range(5):
        _discover_once(state, None)

    assert len(state.jobs) == baseline_jobs
    assert len(state.approval_queue.all()) == baseline_approvals
    assert len(state.documents) == baseline_approvals


def test_every_approval_resolves_to_a_live_job_after_repeated_sweeps():
    """Orphaned approvals are what silently killed autonomous auto-apply."""
    state = _state()
    _execute_hunt(state, None)
    for _ in range(3):
        _discover_once(state, None)

    live = {j["job_id"] for j in state.jobs}
    assert all(r.job_id in live for r in state.approval_queue.all())


# ---------------------------------------------------------- orphan pruning

def test_restore_prunes_orphaned_approvals_and_documents():
    """Repairs workspaces already damaged by the unstable-id bug."""
    state = _state()
    _execute_hunt(state, None)
    real_job_id = state.jobs[0]["job_id"]

    state.approval_queue.submit(
        job_id="ghost:999", document_id="doc-ghost",
        company="Nowhere", title="Ghost Role",
    )
    state.documents["ghost:999"] = {"job_id": "ghost:999"}
    assert any(r.job_id == "ghost:999" for r in state.approval_queue.all())

    state._prune_orphans()

    assert not any(r.job_id == "ghost:999" for r in state.approval_queue.all())
    assert "ghost:999" not in state.documents
    assert real_job_id in state.documents


def test_prune_is_a_noop_before_anything_is_discovered():
    """An empty job list means "nothing fetched yet", not "all orphaned"."""
    state = _state()
    state.approval_queue.submit(
        job_id="j1", document_id="d1", company="C", title="T",
    )
    state.documents["j1"] = {"job_id": "j1"}
    state._prune_orphans()
    assert len(state.approval_queue.all()) == 1
    assert "j1" in state.documents


# ------------------------------------------------------- github username

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ada", "ada"),
        ("  ada  ", "ada"),
        ("@ada", "ada"),
        ("github.com/ada", "ada"),
        ("www.github.com/ada", "ada"),
        ("https://github.com/ada", "ada"),
        ("https://github.com/ada/", "ada"),
        ("http://github.com/ada/some-repo", "ada"),
        ("https://github.com/ada?tab=repositories", "ada"),
        ("", ""),
    ],
)
def test_github_username_normalisation(raw, expected):
    assert _github_username(raw) == expected
