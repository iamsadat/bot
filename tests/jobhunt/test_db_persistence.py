"""Tests for database persistence layer (Phase 1.5).

Validates that SQLAlchemy models and Postgres-backed TraceStore work correctly.
Uses in-memory SQLite for fast, offline testing.
"""

import pytest
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobhunt.db.models import Base, User, ReasoningTrace as TraceModel, ToolCall
from jobhunt.db.store import PostgresTraceStore
from jobhunt.models import ReasoningTrace, ToolCall as ToolCallModel


@pytest.fixture
def sqlite_session():
    """Create a fresh in-memory SQLite engine and session for each test."""
    engine = sa_create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_user_model_basic(sqlite_session):
    """Test creating and querying a User."""
    user = User(
        user_id="u-test-1",
        name="Test User",
        email="test@example.com",
        target_roles=["backend engineer"],
        locations=["Remote"],
        remote_ok=True,
        skills=["python", "postgres"],
        experiences=[],
        veto_companies=[],
    )
    sqlite_session.add(user)
    sqlite_session.commit()

    # Query back
    found = sqlite_session.query(User).filter(User.user_id == "u-test-1").first()
    assert found is not None
    assert found.name == "Test User"
    assert found.email == "test@example.com"
    assert "python" in found.skills


def test_trace_append(sqlite_session):
    """Test appending traces to Postgres-backed store."""
    store = PostgresTraceStore(sqlite_session)

    trace = ReasoningTrace.new(agent="discovery", task_id="task-1")
    trace.thoughts = ["Looking for backend roles", "Found 10 results"]
    trace.decision = "Proceeding to vetting"
    trace.confidence = 0.95

    store.append(trace)

    # Verify it was persisted
    retrieved = store.get_by_id(trace.trace_id)
    assert retrieved is not None
    assert retrieved.agent == "discovery"
    assert retrieved.decision == "Proceeding to vetting"
    assert len(retrieved.thoughts) == 2


def test_trace_all_ordered(sqlite_session):
    """Test retrieving all traces in order."""
    store = PostgresTraceStore(sqlite_session)

    trace1 = ReasoningTrace.new(agent="strategy", task_id="task-1")
    trace1.decision = "First"
    store.append(trace1)

    trace2 = ReasoningTrace.new(agent="discovery", task_id="task-1")
    trace2.decision = "Second"
    store.append(trace2)

    all_traces = store.all()
    assert len(all_traces) == 2
    assert all_traces[0].agent == "strategy"
    assert all_traces[1].agent == "discovery"


def test_trace_for_agent(sqlite_session):
    """Test filtering traces by agent."""
    store = PostgresTraceStore(sqlite_session)

    trace1 = ReasoningTrace.new(agent="discovery", task_id="task-1")
    store.append(trace1)

    trace2 = ReasoningTrace.new(agent="discovery", task_id="task-1")
    store.append(trace2)

    trace3 = ReasoningTrace.new(agent="vetting", task_id="task-1")
    store.append(trace3)

    discovery_traces = store.for_agent("discovery")
    assert len(discovery_traces) == 2
    assert all(t.agent == "discovery" for t in discovery_traces)

    vetting_traces = store.for_agent("vetting")
    assert len(vetting_traces) == 1


def test_trace_with_tool_calls(sqlite_session):
    """Test that tool calls are persisted with traces."""
    store = PostgresTraceStore(sqlite_session)

    trace = ReasoningTrace.new(agent="discovery", task_id="task-1")
    trace.tool_calls = [
        ToolCallModel(tool="search", args_summary="q=backend", ok=True, latency_ms=145),
        ToolCallModel(tool="filter", args_summary="min_salary=150k", ok=True, latency_ms=52),
    ]
    trace.decision = "Found matches"

    store.append(trace)

    retrieved = store.get_by_id(trace.trace_id)
    assert retrieved is not None
    assert len(retrieved.tool_calls) == 2
    assert retrieved.tool_calls[0].tool == "search"
    assert retrieved.tool_calls[1].tool == "filter"


def test_pii_redaction_on_append(sqlite_session):
    """Test that PII is redacted before persisting."""
    store = PostgresTraceStore(sqlite_session)

    trace = ReasoningTrace.new(agent="discovery", task_id="task-1")
    trace.thoughts = ["Found role at alice@example.com", "Contact: +1-555-1234"]
    trace.decision = "Email bob@test.com about offer"

    store.append(trace)

    retrieved = store.get_by_id(trace.trace_id)
    assert "<email>" in retrieved.thoughts[0]
    assert "<phone>" in retrieved.thoughts[1]
    assert "<email>" in retrieved.decision
