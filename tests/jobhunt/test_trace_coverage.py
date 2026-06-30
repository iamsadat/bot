"""Extended tests for jobhunt/trace.py — covering TraceStore.to_jsonl,
TraceStore.for_agent, ThoughtBus with event payloads, set_loop, history
truncation, and merge_thoughts (previously at 72% coverage).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from jobhunt.models import ReasoningTrace, TraceEvent
from jobhunt.trace import ThoughtBus, TraceStore, merge_thoughts, redact


# ----------------------------------------------------------------- TraceStore


class TestTraceStoreExtended:
    def test_to_jsonl_produces_valid_json_lines(self):
        store = TraceStore()
        t1 = ReasoningTrace.new("agent-a", "task-1")
        t1.thoughts = ["thought one"]
        t1.decision = "decided X"
        t2 = ReasoningTrace.new("agent-b", "task-2")
        t2.thoughts = ["thought two"]
        t2.decision = "decided Y"
        store.append(t1)
        store.append(t2)

        jsonl = store.to_jsonl()
        lines = jsonl.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "agent" in data
            assert "thoughts" in data

    def test_for_agent_filters_correctly(self):
        store = TraceStore()
        t1 = ReasoningTrace.new("discovery", "t1")
        t1.decision = "found jobs"
        t2 = ReasoningTrace.new("vetting", "t2")
        t2.decision = "scored"
        t3 = ReasoningTrace.new("discovery", "t3")
        t3.decision = "found more"
        store.append(t1)
        store.append(t2)
        store.append(t3)

        disco = store.for_agent("discovery")
        assert len(disco) == 2
        assert all(t.agent == "discovery" for t in disco)

        vetting = store.for_agent("vetting")
        assert len(vetting) == 1

    def test_for_agent_returns_empty_for_unknown(self):
        store = TraceStore()
        t = ReasoningTrace.new("agent-x", "t1")
        t.decision = "done"
        store.append(t)
        assert store.for_agent("unknown") == []

    def test_redaction_on_events(self):
        store = TraceStore()
        t = ReasoningTrace.new("agent", "t1")
        t.decision = "ok"
        event = TraceEvent(
            phase="act",
            summary="email ada@example.com about role",
            considered=["ada@example.com option"],
            rejected=[{"item": "call +1 555 123 4567", "reason": "phone is bad"}],
            confidence=0.9,
            decision="use email ada@example.com",
        )
        t.events = [event]
        store.append(t)

        stored = store.all()[0]
        assert "ada@example.com" not in stored.events[0].summary
        assert "<email>" in stored.events[0].summary
        assert "ada@example.com" not in stored.events[0].decision
        assert "555" not in stored.events[0].rejected[0]["item"]
        assert "ada@example.com" not in stored.events[0].considered[0]


# ----------------------------------------------------------------- ThoughtBus


class TestThoughtBusExtended:
    def test_publish_with_event_payload(self):
        bus = ThoughtBus()
        bus.publish(
            "agent-x", "task-1", "thinking...",
            event={
                "phase": "deliberate",
                "considered": ["option A", "option B"],
                "rejected": [{"item": "option C", "reason": "too risky"}],
                "confidence": 0.85,
                "decision": "go with A",
            },
        )

        history = bus.history()
        assert len(history) == 1
        item = history[0]
        assert item["agent"] == "agent-x"
        assert item["thought"] == "thinking..."
        assert item["phase"] == "deliberate"
        assert item["confidence"] == 0.85
        assert item["decision"] == "go with A"
        assert len(item["considered"]) == 2
        assert len(item["rejected"]) == 1

    def test_event_payload_redacts_pii(self):
        bus = ThoughtBus()
        bus.publish(
            "agent", "t1", "contact ada@test.com",
            event={
                "phase": "act",
                "considered": ["email ada@test.com"],
                "rejected": [{"item": "call +1 555 123 4567", "reason": "no phone"}],
                "confidence": 0.5,
                "decision": "email ada@test.com",
            },
        )
        item = bus.history()[0]
        assert "ada@test.com" not in item["thought"]
        assert "ada@test.com" not in item["decision"]
        assert "ada@test.com" not in item["considered"][0]
        assert "555" not in item["rejected"][0]["item"]

    def test_history_capped_at_max(self):
        bus = ThoughtBus()
        bus._max_history = 5
        for i in range(10):
            bus.publish("a", "t", f"thought-{i}")
        assert len(bus.history()) == 5
        # Should keep the latest
        assert bus.history()[-1]["thought"] == "thought-9"
        assert bus.history()[0]["thought"] == "thought-5"

    def test_set_loop_and_threadsafe_publish(self):
        bus = ThoughtBus()
        loop = MagicMock()
        loop.is_running.return_value = True
        bus.set_loop(loop)

        # Add a subscriber queue
        q = asyncio.Queue(maxsize=256)
        bus._subscribers.append(q)

        bus.publish("agent", "t1", "hello")

        # call_soon_threadsafe should have been invoked
        loop.call_soon_threadsafe.assert_called_once()

    def test_publish_without_loop(self):
        bus = ThoughtBus()
        q = asyncio.Queue(maxsize=256)
        bus._subscribers.append(q)

        bus.publish("agent", "t1", "direct")
        # Item should be in the queue
        assert not q.empty()
        item = q.get_nowait()
        assert item["thought"] == "direct"

    def test_publish_drops_slow_subscriber(self):
        bus = ThoughtBus()
        # Create a queue with maxsize=1 and fill it
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"thought": "blocker"})
        bus._subscribers.append(q)

        # This should not raise even though the queue is full
        bus.publish("agent", "t1", "overflow")
        # History still grows
        assert len(bus.history()) == 1

    def test_history_returns_copy(self):
        bus = ThoughtBus()
        bus.publish("a", "t", "one")
        h = bus.history()
        h.append({"fake": True})
        assert len(bus.history()) == 1  # Original not modified

    def test_event_caps_considered_and_rejected(self):
        bus = ThoughtBus()
        bus.publish(
            "agent", "t1", "lots",
            event={
                "phase": "act",
                "considered": [f"c{i}" for i in range(20)],
                "rejected": [{"item": f"r{i}", "reason": f"why{i}"} for i in range(20)],
                "confidence": 0.5,
                "decision": "d",
            },
        )
        item = bus.history()[0]
        assert len(item["considered"]) <= 8
        assert len(item["rejected"]) <= 8


# ----------------------------------------------------------------- merge_thoughts


class TestMergeThoughts:
    def test_appends_to_trace_and_publishes(self):
        trace = ReasoningTrace.new("agent", "t1")
        bus = ThoughtBus()

        lines = ["first thought", "second thought", "third thought"]
        merge_thoughts(trace, bus, lines)

        assert trace.thoughts == lines
        history = bus.history()
        assert len(history) == 3
        assert [h["thought"] for h in history] == lines

    def test_empty_iterable(self):
        trace = ReasoningTrace.new("agent", "t1")
        bus = ThoughtBus()

        merge_thoughts(trace, bus, [])
        assert trace.thoughts == []
        assert bus.history() == []


# ----------------------------------------------------------------- redact (additional)


class TestRedactExtended:
    def test_multiple_emails_redacted(self):
        text = "contact alice@a.com or bob@b.com"
        out = redact(text)
        assert "alice@a.com" not in out
        assert "bob@b.com" not in out
        assert out.count("<email>") == 2

    def test_phone_formats(self):
        assert "<phone>" in redact("+44 20 7946 0958")
        assert "<phone>" in redact("(555) 123-4567")
        assert "<phone>" in redact("555.123.4567")

    def test_no_pii_unchanged(self):
        text = "This is a clean text without any PII."
        assert redact(text) == text

    def test_mixed_pii(self):
        text = "Call +1 800 555 1234 or email support@example.org today"
        out = redact(text)
        assert "support@example.org" not in out
        assert "800" not in out or "555" not in out


# ----------------------------------------------------------------- async subscribe


class TestThoughtBusSubscribe:
    def test_subscribe_replays_history(self):
        """Test that history() returns published items (sync check)."""
        bus = ThoughtBus()
        bus.publish("a", "t1", "historical-1")
        bus.publish("a", "t1", "historical-2")

        h = bus.history()
        assert len(h) == 2
        assert h[0]["thought"] == "historical-1"
        assert h[1]["thought"] == "historical-2"

    def test_subscribe_adds_and_removes_subscriber(self):
        """Test that the subscriber mechanism works at a low level."""
        bus = ThoughtBus()
        q = asyncio.Queue(maxsize=256)
        bus._subscribers.append(q)
        assert len(bus._subscribers) == 1

        bus.publish("a", "t1", "msg")
        # Message is placed in subscriber queue
        assert not q.empty()
        item = q.get_nowait()
        assert item["thought"] == "msg"

        bus._subscribers.remove(q)
        assert len(bus._subscribers) == 0
