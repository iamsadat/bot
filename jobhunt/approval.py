"""Approval workflow for tailored documents.

The Resume Architect emits a TailoredDocument with
``requires_human_approval = True``. The Submission Agent never auto-fires;
it pulls from this queue. Reviewers (humans on the dashboard) advance the
state machine via approve / reject / edit_requested.

States:
  pending → approved → submitted
  pending → rejected
  pending → edit_requested → pending (after revisions)

The queue is pluggable: in-memory for the demo, Redis-backed for prod.
Events publish to a "approvals" pub/sub channel so the dashboard's
WebSocket stream stays in sync.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional

from jobhunt.redis_client import BaseRedisClient, FakeRedisClient


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDIT_REQUESTED = "edit_requested"
    SUBMITTED = "submitted"


@dataclass
class ApprovalRequest:
    request_id: str
    job_id: str
    document_id: str
    company: str
    title: str
    state: ApprovalState = ApprovalState.PENDING
    reviewer: str = ""
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["state"] = self.state.value
        return out


_VALID_TRANSITIONS: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.PENDING: {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.EDIT_REQUESTED,
    },
    ApprovalState.EDIT_REQUESTED: {ApprovalState.PENDING, ApprovalState.REJECTED},
    ApprovalState.APPROVED: {ApprovalState.SUBMITTED},
    ApprovalState.REJECTED: set(),
    ApprovalState.SUBMITTED: set(),
}


class InvalidTransition(ValueError):
    """Raised on an illegal state transition (e.g. submitted → pending)."""


class ApprovalQueue:
    """In-memory queue with optional Redis-backed pub/sub for events.

    All mutations also publish an event to the ``approvals`` channel so the
    dashboard's WebSocket can fan it out to subscribers.
    """

    PUBSUB_CHANNEL = "approvals"

    def __init__(self, redis: Optional[BaseRedisClient] = None) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self.redis = redis or FakeRedisClient()
        self._listeners: list[Callable[[ApprovalRequest], None]] = []

    # ------------------------------------------------------------------ CRUD

    def submit(
        self, *, job_id: str, document_id: str, company: str, title: str,
    ) -> ApprovalRequest:
        """Create a new pending approval request."""
        req = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            job_id=job_id,
            document_id=document_id,
            company=company,
            title=title,
        )
        self._items[req.request_id] = req
        self._emit(req, event="created")
        return req

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._items.get(request_id)

    def by_job(self, job_id: str) -> list[ApprovalRequest]:
        return [r for r in self._items.values() if r.job_id == job_id]

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self._items.values() if r.state == ApprovalState.PENDING]

    def all(self) -> list[ApprovalRequest]:
        return list(self._items.values())

    # ----------------------------------------------------------- transitions

    def transition(
        self,
        request_id: str,
        new_state: ApprovalState,
        *,
        reviewer: str = "",
        notes: str = "",
    ) -> ApprovalRequest:
        """Move a request to ``new_state``.

        Raises:
            KeyError: unknown request_id.
            InvalidTransition: transition not allowed from current state.
        """
        req = self._items.get(request_id)
        if req is None:
            raise KeyError(f"unknown approval request: {request_id}")
        if new_state not in _VALID_TRANSITIONS.get(req.state, set()):
            raise InvalidTransition(
                f"cannot move {req.request_id} from {req.state} to {new_state}"
            )
        req.state = new_state
        if reviewer:
            req.reviewer = reviewer
        if notes:
            req.notes = notes
        req.updated_at = time.time()
        self._emit(req, event=new_state.value)
        return req

    # Convenience wrappers (mirror the dashboard's verbs).

    def approve(self, request_id: str, reviewer: str = "") -> ApprovalRequest:
        return self.transition(
            request_id, ApprovalState.APPROVED, reviewer=reviewer,
        )

    def reject(self, request_id: str, reviewer: str = "", notes: str = "") -> ApprovalRequest:
        return self.transition(
            request_id, ApprovalState.REJECTED, reviewer=reviewer, notes=notes,
        )

    def request_edits(
        self, request_id: str, reviewer: str = "", notes: str = "",
    ) -> ApprovalRequest:
        return self.transition(
            request_id, ApprovalState.EDIT_REQUESTED,
            reviewer=reviewer, notes=notes,
        )

    def mark_submitted(self, request_id: str) -> ApprovalRequest:
        return self.transition(request_id, ApprovalState.SUBMITTED)

    # ----------------------------------------------------------- listeners

    def subscribe(self, listener: Callable[[ApprovalRequest], None]) -> None:
        """Register a synchronous in-process listener (e.g. submission agent)."""
        self._listeners.append(listener)

    def _emit(self, req: ApprovalRequest, *, event: str) -> None:
        payload = {"event": event, "request": req.to_dict()}
        try:
            self.redis.publish(self.PUBSUB_CHANNEL, payload)
        except Exception:
            # Pub/sub is best-effort — don't let a Redis hiccup block the queue.
            pass
        for listener in list(self._listeners):
            try:
                listener(req)
            except Exception:
                continue


# -------------------------------------------------------- agent integration


def auto_enqueue_documents(
    queue: ApprovalQueue,
    documents: Iterable,
) -> list[ApprovalRequest]:
    """Bulk-enqueue documents emitted by the Resume Architect.

    Accepts any object with ``job_id``, ``company``, ``title``, plus
    either ``document_id`` or sufficient identifiers to form one.
    """
    requests: list[ApprovalRequest] = []
    for d in documents:
        document_id = getattr(d, "document_id", None) or getattr(d, "job_id", "")
        req = queue.submit(
            job_id=getattr(d, "job_id", ""),
            document_id=document_id,
            company=getattr(d, "company", ""),
            title=getattr(d, "title", ""),
        )
        requests.append(req)
    return requests
