"""Adapter-level rate limiting — pure stdlib, no external deps.

Provides a :class:`RateLimiter` (token-bucket / fixed-interval) that can
be injected into :class:`~jobhunt.http.RateLimitedHTTPClient` to enforce
polite per-source request pacing.

The clock and sleep callables are injectable so tests can pass fakes and
run instantly.
"""

from __future__ import annotations

import math
import time as _time
from typing import Callable


class RateLimiter:
    """Token-bucket rate limiter.

    Parameters
    ----------
    max_calls:
        Maximum number of calls allowed within ``per_seconds``.
    per_seconds:
        Window length in seconds.
    clock:
        Callable returning current time as a float.  Defaults to
        :func:`time.monotonic`.
    sleep:
        Callable that blocks for the given number of seconds.  Defaults to
        :func:`time.sleep`.
    """

    def __init__(
        self,
        *,
        max_calls: int,
        per_seconds: float,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if per_seconds <= 0:
            raise ValueError("per_seconds must be > 0")
        self._max_calls = max_calls
        self._per_seconds = per_seconds
        self._clock = clock if clock is not None else _time.monotonic
        self._sleep = sleep if sleep is not None else _time.sleep
        # Ring buffer of timestamps for the last max_calls acquisitions.
        self._timestamps: list[float] = []

    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            now = self._clock()
            # Evict timestamps outside the current window.
            window_start = now - self._per_seconds
            self._timestamps = [t for t in self._timestamps if t > window_start]

            if len(self._timestamps) < self._max_calls:
                # Token available — record and return.
                self._timestamps.append(now)
                return

            # Blocked: compute how long until the oldest token expires.
            oldest = self._timestamps[0]
            wait = oldest - window_start
            # Small epsilon guard against sleeping 0 or negative.
            self._sleep(max(wait, 1e-9))
