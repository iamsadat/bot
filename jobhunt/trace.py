"""Reasoning trace store and thought-stream bus.

The store is append-only. In production this is Postgres + S3 Parquet
mirror (ARCHITECTURE.md §4). The in-memory implementation here keeps the
same interface so swapping later is a one-file change.

The ThoughtBus is the realtime channel that feeds the dashboard's
``Live thought stream`` panel. Subscribers receive every appended thought
in order; the bus is async so FastAPI can use it directly.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from typing import AsyncIterator, Iterable

from jobhunt.models import ReasoningTrace


# --------------------------------------------------------------- redaction

_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b"), "<phone>"),
]


def redact(text: str) -> str:
    """Strip obvious PII before traces hit any log or trace sink."""
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


# ----------------------------------------------------------------- storage

class TraceStore:
    """Append-only in-memory trace store. Production: Postgres."""

    def __init__(self) -> None:
        self._traces: list[ReasoningTrace] = []

    def append(self, trace: ReasoningTrace) -> None:
        # Defensive copy of thoughts so later mutations don't change history.
        trace.thoughts = [redact(t) for t in trace.thoughts]
        trace.decision = redact(trace.decision)
        self._traces.append(trace)

    def all(self) -> list[ReasoningTrace]:
        return list(self._traces)

    def for_agent(self, agent: str) -> list[ReasoningTrace]:
        return [t for t in self._traces if t.agent == agent]

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(asdict(t)) for t in self._traces)


# ------------------------------------------------------------- thought bus

class ThoughtBus:
    """Async fan-out for reasoning thoughts. Backs the WebSocket stream."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._history: list[dict] = []
        self._max_history = 500

    def publish(self, agent: str, task_id: str, thought: str) -> None:
        payload = {"agent": agent, "task_id": task_id, "thought": redact(thought)}
        self._history.append(payload)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop slow subscribers rather than block the agent.
                pass

    def history(self) -> list[dict]:
        return list(self._history)

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        try:
            for item in self._history:
                yield item
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)


# ----------------------------------------------------------- helper merge

def merge_thoughts(trace: ReasoningTrace, bus: ThoughtBus, lines: Iterable[str]) -> None:
    for line in lines:
        trace.thoughts.append(line)
        bus.publish(trace.agent, trace.task_id, line)
