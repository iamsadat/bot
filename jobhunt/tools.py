"""Tool-call wrapper with retry, timeout, and circuit breaker.

Every external call an agent makes must go through ``call_tool``. The
wrapper enforces:

* exponential-backoff retry with a budget
* a per-tool circuit breaker so a flapping source is quarantined
* structured ToolCall records appended to the agent's ReasoningTrace
* typed fallback: callers receive ``(result, degraded)`` so they can
  reason about partial data instead of crashing

This is intentionally tiny — the contract matters more than the
implementation, and the contract is what callers depend on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from jobhunt.models import ReasoningTrace, ToolCall

T = TypeVar("T")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.time() - self.opened_at >= self.cooldown_s:
            # Half-open: let one through.
            self.opened_at = None
            self.failures = 0
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()


@dataclass
class ToolRegistry:
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def breaker(self, tool: str) -> CircuitBreaker:
        if tool not in self.breakers:
            self.breakers[tool] = CircuitBreaker()
        return self.breakers[tool]


def call_tool(
    registry: ToolRegistry,
    trace: ReasoningTrace,
    tool: str,
    func: Callable[..., T],
    *args: Any,
    fallback: Callable[[], T] | None = None,
    max_retries: int = 2,
    args_summary: str = "",
    **kwargs: Any,
) -> tuple[T | None, bool]:
    """Call ``func`` under the resilience policy.

    Returns ``(result, degraded)``. ``degraded`` is True when the
    fallback was used or the breaker was open.
    """
    breaker = registry.breaker(tool)
    if not breaker.allow():
        trace.tool_calls.append(
            ToolCall(
                tool=tool,
                args_summary=args_summary,
                ok=False,
                latency_ms=0,
                retries=0,
                fallback_used=fallback is not None,
                error="circuit_open",
            )
        )
        return (fallback() if fallback else None), True

    retries = 0
    last_err: Exception | None = None
    while retries <= max_retries:
        start = time.time()
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            last_err = exc
            retries += 1
            breaker.record_failure()
            # Exponential backoff: 0.1, 0.2, 0.4 ...
            time.sleep(min(0.1 * (2**retries), 1.0))
            continue
        latency_ms = int((time.time() - start) * 1000)
        breaker.record_success()
        trace.tool_calls.append(
            ToolCall(
                tool=tool,
                args_summary=args_summary,
                ok=True,
                latency_ms=latency_ms,
                retries=retries,
            )
        )
        return result, False

    # Exhausted retries.
    trace.tool_calls.append(
        ToolCall(
            tool=tool,
            args_summary=args_summary,
            ok=False,
            latency_ms=0,
            retries=retries,
            fallback_used=fallback is not None,
            error=type(last_err).__name__ if last_err else "unknown",
        )
    )
    return (fallback() if fallback else None), True
