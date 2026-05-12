import asyncio

from jobhunt.models import ReasoningTrace
from jobhunt.trace import ThoughtBus, TraceStore, redact


def test_redact_strips_email_and_phone():
    out = redact("contact ada@example.com or +1 (555) 123-4567")
    assert "ada@example.com" not in out
    assert "555" not in out
    assert "<email>" in out and "<phone>" in out


def test_trace_store_is_append_only_and_redacts():
    store = TraceStore()
    t = ReasoningTrace.new("test", "task-1")
    t.thoughts.append("contact ada@example.com about role")
    t.decision = "send to ada@example.com"
    store.append(t)
    [stored] = store.all()
    assert "ada@example.com" not in stored.thoughts[0]
    assert "ada@example.com" not in stored.decision


def test_thought_bus_replays_history_to_new_subscribers():
    bus = ThoughtBus()
    bus.publish("a", "t1", "first")
    bus.publish("a", "t1", "second")

    async def collect():
        out = []
        agen = bus.subscribe()
        # Consume the two replayed items.
        async for item in agen:
            out.append(item)
            if len(out) == 2:
                break
        return out

    items = asyncio.run(collect())
    assert [i["thought"] for i in items] == ["first", "second"]
