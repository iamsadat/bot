"""Base agent: the shared skeleton every agent runs.

The contract codifies the reasoning requirements from the spec:
pre-action deliberation, tool-use with retry, self-critique, structured
output, and a persisted ReasoningTrace.

Subclasses implement four hooks:

* ``deliberate``  — pre-action reasoning bullets
* ``act``         — the actual work (calls tools via ``self.call_tool``)
* ``critique``    — self-check scores against a checklist
* ``decide``      — final structured decision string + confidence

``run`` glues them together, manages the trace, and enforces a quality
threshold with a bounded refinement loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from jobhunt.models import ReasoningTrace, TraceEvent
from jobhunt.tools import ToolRegistry, call_tool
from jobhunt.trace import ThoughtBus, TraceStore

I = TypeVar("I")
O = TypeVar("O")


@dataclass
class AgentResult(Generic[O]):
    output: O | None
    trace: ReasoningTrace
    refined: int = 0
    degraded: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


class BaseAgent(Generic[I, O]):
    """Shared run loop. Subclass and implement the four hooks."""

    name: str = "base"

    # Quality threshold: average self-critique score required to accept the
    # output. Subclasses tighten this for safety-critical work.
    quality_threshold: float = 0.7
    max_refinements: int = 1

    def __init__(
        self,
        trace_store: TraceStore,
        bus: ThoughtBus,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.trace_store = trace_store
        self.bus = bus
        self.tools = tools or ToolRegistry()

    # ----- hooks (override) -------------------------------------------------

    def deliberate(self, inputs: I, trace: ReasoningTrace) -> list[str]:
        """Return reasoning bullets describing the plan before acting."""
        return []

    def act(self, inputs: I, trace: ReasoningTrace) -> O:
        raise NotImplementedError

    def critique(self, inputs: I, output: O, trace: ReasoningTrace) -> dict[str, float]:
        """Return a {criterion: score in 0..1} self-check."""
        return {}

    def decide(
        self, inputs: I, output: O, scores: dict[str, float], trace: ReasoningTrace
    ) -> tuple[str, float]:
        """Final decision string + confidence in 0..1."""
        avg = sum(scores.values()) / len(scores) if scores else 1.0
        return ("accept", avg)

    # ----- machinery --------------------------------------------------------

    def think(self, trace: ReasoningTrace, line: str) -> None:
        # Thin wrapper over emit() for plain status lines (phase=act).
        self.emit(trace, "act", line)

    def emit(
        self,
        trace: ReasoningTrace,
        phase: str,
        summary: str,
        *,
        considered: list[str] | None = None,
        rejected: list[dict[str, str]] | None = None,
        confidence: float | None = None,
        decision: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a substantive reasoning step + publish it to the bus.

        Appends a structured ``TraceEvent`` (what was considered/rejected/why,
        confidence, decision) AND mirrors ``summary`` into ``trace.thoughts`` so
        anything reading the old flat shape keeps working.
        """
        ev = TraceEvent(
            phase=phase, summary=summary,
            considered=list(considered or []),
            rejected=list(rejected or []),
            confidence=confidence, decision=decision,
            data=dict(data or {}),
        )
        trace.events.append(ev)
        trace.thoughts.append(summary)
        self.bus.publish(
            self.name, trace.task_id, summary,
            event={
                "phase": phase, "considered": ev.considered,
                "rejected": ev.rejected, "confidence": confidence,
                "decision": decision,
            },
        )

    def call_tool(
        self,
        trace: ReasoningTrace,
        tool: str,
        func: Callable[..., Any],
        *args: Any,
        fallback: Callable[[], Any] | None = None,
        args_summary: str = "",
        **kwargs: Any,
    ) -> tuple[Any, bool]:
        return call_tool(
            self.tools,
            trace,
            tool,
            func,
            *args,
            fallback=fallback,
            args_summary=args_summary,
            **kwargs,
        )

    def run(
        self, inputs: I, task_id: str, parent_trace: str | None = None
    ) -> AgentResult[O]:
        trace = ReasoningTrace.new(self.name, task_id, parent=parent_trace)

        # Pre-action deliberation.
        bullets = self.deliberate(inputs, trace)
        for b in bullets:
            self.emit(trace, "deliberate", b)

        # Act + critique + (optional) refine.
        output = self.act(inputs, trace)
        scores = self.critique(inputs, output, trace)
        trace.self_critique = dict(scores)
        if scores:
            self.emit(
                trace, "critique",
                "self-review: " + ", ".join(f"{k}={v:.2f}" for k, v in scores.items()),
                confidence=round(sum(scores.values()) / len(scores), 3),
                data={"scores": scores},
            )

        refined = 0
        while (
            scores
            and (sum(scores.values()) / len(scores)) < self.quality_threshold
            and refined < self.max_refinements
        ):
            self.think(
                trace,
                f"self-critique below threshold ({sum(scores.values())/len(scores):.2f}"
                f" < {self.quality_threshold}); refining (attempt {refined+1}).",
            )
            output = self.act(inputs, trace)
            scores = self.critique(inputs, output, trace)
            trace.self_critique = dict(scores)
            refined += 1

        decision, confidence = self.decide(inputs, output, scores, trace)
        trace.decision = decision
        trace.confidence = confidence
        self.emit(trace, "decide", f"decision: {decision}",
                  confidence=confidence, decision=decision)

        degraded = any(tc.fallback_used or not tc.ok for tc in trace.tool_calls)

        self.trace_store.append(trace)
        return AgentResult(
            output=output, trace=trace, refined=refined, degraded=degraded
        )
