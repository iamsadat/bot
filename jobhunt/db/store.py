"""Postgres-backed trace store replacing in-memory implementation.

Maintains the same interface as jobhunt.trace.TraceStore but persists to Postgres.
All operations are transactional and append-only (immutable audit log).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from jobhunt.db.models import ReasoningTrace as TraceModel, ToolCall as ToolCallModel
from jobhunt.models import ReasoningTrace, ToolCall
from jobhunt.trace import redact


class PostgresTraceStore:
    """Append-only Postgres-backed trace store.

    Same interface as in-memory TraceStore but persists to DB.
    All methods are transactional.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, trace: ReasoningTrace) -> None:
        """Append a trace and its tool calls to the database (atomically).

        PII is redacted before insertion.
        """
        # Redact PII in-memory
        trace.thoughts = [redact(t) for t in trace.thoughts]
        trace.decision = redact(trace.decision)

        # Create ORM trace
        db_trace = TraceModel(
            trace_id=trace.trace_id,
            agent=trace.agent,
            task_id=trace.task_id,
            thoughts=trace.thoughts,
            self_critique=trace.self_critique,
            decision=trace.decision,
            confidence=trace.confidence,
            parent_trace_id=trace.parent_trace_id,
            created_at=datetime.fromtimestamp(trace.created_at),
        )
        self.session.add(db_trace)

        # Insert tool calls
        for tc in trace.tool_calls:
            db_tool_call = ToolCallModel(
                tool_call_id=uuid.uuid4().hex,
                trace_id=trace.trace_id,
                tool=tc.tool,
                args_summary=tc.args_summary,
                ok=tc.ok,
                latency_ms=tc.latency_ms,
                retries=tc.retries,
                fallback_used=tc.fallback_used,
                error=tc.error,
                created_at=datetime.fromtimestamp(trace.created_at),
            )
            self.session.add(db_tool_call)

        self.session.commit()

    def all(self) -> list[ReasoningTrace]:
        """Return all traces ordered by creation time."""
        rows = self.session.query(TraceModel).order_by(TraceModel.created_at).all()
        return [self._row_to_trace(row) for row in rows]

    def for_agent(self, agent: str) -> list[ReasoningTrace]:
        """Return all traces for a specific agent."""
        rows = (
            self.session.query(TraceModel)
            .filter(TraceModel.agent == agent)
            .order_by(TraceModel.created_at)
            .all()
        )
        return [self._row_to_trace(row) for row in rows]

    def get_by_id(self, trace_id: str) -> ReasoningTrace | None:
        """Fetch a single trace by ID."""
        row = self.session.query(TraceModel).filter(TraceModel.trace_id == trace_id).first()
        return self._row_to_trace(row) if row else None

    def for_task(self, task_id: str) -> list[ReasoningTrace]:
        """Return all traces for a specific task."""
        rows = (
            self.session.query(TraceModel)
            .filter(TraceModel.task_id == task_id)
            .order_by(TraceModel.created_at)
            .all()
        )
        return [self._row_to_trace(row) for row in rows]

    def _row_to_trace(self, row: TraceModel) -> ReasoningTrace:
        """Convert ORM row to dataclass."""
        # Fetch tool calls
        tool_calls = (
            self.session.query(ToolCallModel)
            .filter(ToolCallModel.trace_id == row.trace_id)
            .order_by(ToolCallModel.created_at)
            .all()
        )
        tool_call_list = [
            ToolCall(
                tool=tc.tool,
                args_summary=tc.args_summary,
                ok=tc.ok,
                latency_ms=tc.latency_ms,
                retries=tc.retries,
                fallback_used=tc.fallback_used,
                error=tc.error,
            )
            for tc in tool_calls
        ]

        return ReasoningTrace(
            trace_id=row.trace_id,
            agent=row.agent,
            task_id=row.task_id,
            thoughts=row.thoughts or [],
            tool_calls=tool_call_list,
            self_critique=row.self_critique or {},
            decision=row.decision or "",
            confidence=row.confidence,
            parent_trace_id=row.parent_trace_id,
            created_at=row.created_at.timestamp() if row.created_at else 0.0,
        )
