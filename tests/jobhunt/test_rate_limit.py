"""Offline tests for RateLimiter and RateLimitedHTTPClient.

All tests use a fake clock and fake sleep so no real time passes.
"""

from __future__ import annotations

import pytest

from jobhunt.http import FakeHTTPClient, HTTPClientError, RateLimitedHTTPClient
from jobhunt.rate_limit import RateLimiter


# ---------------------------------------------------------------------------
# Fake clock helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Monotonic clock whose time advances only when we say so."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


# ---------------------------------------------------------------------------
# RateLimiter tests
# ---------------------------------------------------------------------------

def test_rate_limiter_allows_max_calls_in_window():
    """max_calls acquisitions within a window must succeed without sleeping."""
    sleeps: list[float] = []
    clock = FakeClock()

    limiter = RateLimiter(
        max_calls=3,
        per_seconds=1.0,
        clock=clock,
        sleep=sleeps.append,
    )

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert sleeps == [], "no sleep expected within quota"


def test_rate_limiter_blocks_beyond_quota():
    """The 4th call in a 3/s bucket must trigger a sleep."""
    sleeps: list[float] = []
    clock = FakeClock(start=0.0)

    def fake_sleep(secs: float) -> None:
        sleeps.append(secs)
        # Advance the clock so the window expires and the next loop
        # iteration finds a token available.
        clock.advance(secs + 0.001)

    limiter = RateLimiter(
        max_calls=3,
        per_seconds=1.0,
        clock=clock,
        sleep=fake_sleep,
    )

    for _ in range(3):
        limiter.acquire()

    limiter.acquire()  # 4th call — must sleep

    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_rate_limiter_sleep_duration_is_positive():
    """The sleep interval is always > 0."""
    sleeps: list[float] = []
    clock = FakeClock(start=10.0)

    def fake_sleep(secs: float) -> None:
        sleeps.append(secs)
        clock.advance(secs + 0.001)

    limiter = RateLimiter(
        max_calls=1,
        per_seconds=2.0,
        clock=clock,
        sleep=fake_sleep,
    )

    limiter.acquire()  # 1st — free
    limiter.acquire()  # 2nd — must wait

    assert all(s > 0 for s in sleeps)


def test_rate_limiter_refills_after_window():
    """After the window elapses, the full quota is restored."""
    sleeps: list[float] = []
    clock = FakeClock(start=0.0)

    limiter = RateLimiter(
        max_calls=2,
        per_seconds=1.0,
        clock=clock,
        sleep=sleeps.append,
    )

    # Use up the quota.
    limiter.acquire()
    limiter.acquire()

    # Advance past the window.
    clock.advance(1.5)

    # Both calls should succeed without sleeping.
    limiter.acquire()
    limiter.acquire()

    assert sleeps == [], "no sleep expected after window refill"


def test_rate_limiter_invalid_max_calls_raises():
    with pytest.raises(ValueError, match="max_calls"):
        RateLimiter(max_calls=0, per_seconds=1.0)


def test_rate_limiter_invalid_per_seconds_raises():
    with pytest.raises(ValueError, match="per_seconds"):
        RateLimiter(max_calls=1, per_seconds=-1.0)


# ---------------------------------------------------------------------------
# RateLimitedHTTPClient tests
# ---------------------------------------------------------------------------

def test_rate_limited_client_delegates_get_json():
    """get_json delegates to the inner client and returns its result."""
    inner = FakeHTTPClient(routes={"http://example.com/": {"ok": True}})
    limiter = RateLimiter(
        max_calls=10,
        per_seconds=1.0,
        clock=FakeClock(),
        sleep=lambda _: None,
    )
    client = RateLimitedHTTPClient(inner, limiter)

    result = client.get_json("http://example.com/")

    assert result == {"ok": True}
    assert inner.calls == ["http://example.com/"]


def test_rate_limited_client_delegates_get_text():
    """get_text delegates to the inner client and returns its result."""
    inner = FakeHTTPClient(text_routes={"http://example.com/feed": "<rss/>"})
    limiter = RateLimiter(
        max_calls=10,
        per_seconds=1.0,
        clock=FakeClock(),
        sleep=lambda _: None,
    )
    client = RateLimitedHTTPClient(inner, limiter)

    result = client.get_text("http://example.com/feed")

    assert result == "<rss/>"
    assert inner.calls == ["http://example.com/feed"]


def test_rate_limited_client_calls_acquire_before_request():
    """acquire() is called once per get_json call."""
    acquires: list[None] = []

    class TrackingLimiter:
        def acquire(self) -> None:
            acquires.append(None)

    inner = FakeHTTPClient(routes={"http://example.com/": 42})
    client = RateLimitedHTTPClient(inner, TrackingLimiter())  # type: ignore[arg-type]

    client.get_json("http://example.com/")
    client.get_json("http://example.com/")

    assert len(acquires) == 2


def test_rate_limited_client_propagates_inner_error():
    """Errors from the inner client bubble up unchanged."""

    def boom():
        raise HTTPClientError("boom")

    inner = FakeHTTPClient(routes={"http://example.com/": boom})
    limiter = RateLimiter(
        max_calls=10,
        per_seconds=1.0,
        clock=FakeClock(),
        sleep=lambda _: None,
    )
    client = RateLimitedHTTPClient(inner, limiter)

    with pytest.raises(HTTPClientError, match="boom"):
        client.get_json("http://example.com/")


def test_rate_limited_client_text_propagates_inner_error():
    """Errors from get_text bubble up unchanged."""

    def boom():
        raise HTTPClientError("text boom")

    inner = FakeHTTPClient(text_routes={"http://example.com/feed": boom})
    limiter = RateLimiter(
        max_calls=10,
        per_seconds=1.0,
        clock=FakeClock(),
        sleep=lambda _: None,
    )
    client = RateLimitedHTTPClient(inner, limiter)

    with pytest.raises(HTTPClientError, match="text boom"):
        client.get_text("http://example.com/feed")
