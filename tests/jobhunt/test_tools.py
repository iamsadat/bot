from jobhunt.models import ReasoningTrace
from jobhunt.tools import CircuitBreaker, ToolRegistry, call_tool


def test_retry_and_fallback_on_failure():
    trace = ReasoningTrace.new("a", "t")
    reg = ToolRegistry()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise RuntimeError("boom")

    result, degraded = call_tool(
        reg, trace, "src", flaky, fallback=lambda: [], max_retries=1
    )
    assert result == [] and degraded
    assert calls["n"] == 2  # initial + 1 retry
    [tc] = trace.tool_calls
    assert tc.ok is False and tc.fallback_used is True


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=2, cooldown_s=60)
    cb.record_failure()
    assert cb.allow() is True
    cb.record_failure()
    assert cb.allow() is False  # open


def test_successful_call_records_latency_and_resets_breaker():
    trace = ReasoningTrace.new("a", "t")
    reg = ToolRegistry()
    reg.breaker("src").record_failure()
    result, degraded = call_tool(reg, trace, "src", lambda: 42)
    assert result == 42 and not degraded
    assert reg.breaker("src").failures == 0
    [tc] = trace.tool_calls
    assert tc.ok is True
