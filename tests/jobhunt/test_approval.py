"""Tests for the approval workflow (Phase 2).

Validates the state machine, Redis pub/sub fan-out, and the
``auto_enqueue_documents`` helper used by the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jobhunt.approval import (
    ApprovalQueue,
    ApprovalRequest,
    ApprovalState,
    InvalidTransition,
    auto_enqueue_documents,
)
from jobhunt.redis_client import FakeRedisClient


def _submit(queue: ApprovalQueue) -> ApprovalRequest:
    return queue.submit(
        job_id="j-1", document_id="d-1",
        company="Acme", title="Senior Backend Engineer",
    )


def test_submit_creates_pending_request():
    q = ApprovalQueue()
    req = _submit(q)
    assert req.state == ApprovalState.PENDING
    assert req.company == "Acme"
    assert q.get(req.request_id) is req


def test_approve_transitions_to_approved():
    q = ApprovalQueue()
    req = _submit(q)
    out = q.approve(req.request_id, reviewer="ada")
    assert out.state == ApprovalState.APPROVED
    assert out.reviewer == "ada"


def test_reject_with_notes():
    q = ApprovalQueue()
    req = _submit(q)
    out = q.reject(req.request_id, reviewer="ada", notes="off-brand")
    assert out.state == ApprovalState.REJECTED
    assert out.notes == "off-brand"


def test_request_edits_returns_to_pending_after_revisions():
    q = ApprovalQueue()
    req = _submit(q)
    q.request_edits(req.request_id, notes="tighten the summary")
    # Resume Architect produces a revised draft, queue moves back to pending.
    q.transition(req.request_id, ApprovalState.PENDING)
    assert q.get(req.request_id).state == ApprovalState.PENDING


def test_cannot_skip_states():
    q = ApprovalQueue()
    req = _submit(q)
    with pytest.raises(InvalidTransition):
        q.transition(req.request_id, ApprovalState.SUBMITTED)


def test_cannot_resurrect_rejected():
    q = ApprovalQueue()
    req = _submit(q)
    q.reject(req.request_id)
    with pytest.raises(InvalidTransition):
        q.approve(req.request_id)


def test_pending_filter():
    q = ApprovalQueue()
    a = _submit(q)
    b = q.submit(job_id="j-2", document_id="d-2",
                 company="Globex", title="Staff")
    q.approve(a.request_id)
    pending = q.pending()
    assert {r.request_id for r in pending} == {b.request_id}


def test_pubsub_event_published_on_create():
    redis = FakeRedisClient()
    sub = redis.subscribe(ApprovalQueue.PUBSUB_CHANNEL)
    q = ApprovalQueue(redis=redis)
    _submit(q)
    events = list(sub.queue)
    assert len(events) == 1
    assert events[0]["event"] == "created"
    assert events[0]["request"]["state"] == "pending"


def test_pubsub_event_on_each_transition():
    redis = FakeRedisClient()
    sub = redis.subscribe(ApprovalQueue.PUBSUB_CHANNEL)
    q = ApprovalQueue(redis=redis)
    req = _submit(q)
    q.approve(req.request_id)
    q.mark_submitted(req.request_id)
    events = list(sub.queue)
    states = [e["event"] for e in events]
    assert states == ["created", "approved", "submitted"]


def test_listener_called_on_transitions():
    q = ApprovalQueue()
    seen: list[str] = []
    q.subscribe(lambda r: seen.append(r.state.value))
    req = _submit(q)
    q.approve(req.request_id)
    assert seen == ["pending", "approved"]


def test_listener_exception_does_not_break_queue():
    q = ApprovalQueue()
    q.subscribe(lambda r: (_ for _ in ()).throw(RuntimeError("listener bug")))
    req = _submit(q)  # Should not raise.
    assert req.state == ApprovalState.PENDING


@dataclass
class _Doc:
    job_id: str
    company: str
    title: str
    document_id: str


def test_auto_enqueue_documents_creates_one_per_doc():
    q = ApprovalQueue()
    docs = [
        _Doc("j-1", "Acme", "Backend", "d-1"),
        _Doc("j-2", "Globex", "Staff", "d-2"),
    ]
    requests = auto_enqueue_documents(q, docs)
    assert len(requests) == 2
    assert all(r.state == ApprovalState.PENDING for r in requests)
    assert {r.job_id for r in requests} == {"j-1", "j-2"}
