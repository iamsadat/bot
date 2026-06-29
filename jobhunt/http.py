"""Minimal HTTP client used by job-board adapters.

We keep this stdlib-only so the package has no production dependency
beyond Python itself. The class form is what matters: tests inject a
``FakeHTTPClient`` that returns canned JSON, so adapter logic is
exercised offline.

Real adapters call ``client.get_json(url)`` for JSON APIs or
``client.get_text(url)`` for plain-text / XML feeds (e.g. RSS).  The
client enforces a timeout and surfaces transport failures as
``HTTPClientError`` so the agent-level :func:`jobhunt.tools.call_tool`
wrapper can translate them into degraded results.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from jobhunt.rate_limit import RateLimiter


class HTTPClientError(Exception):
    """Transport-level error from a job-board API."""


class HTTPClient(Protocol):
    def get_json(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> Any: ...
    def get_text(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> str: ...


class UrllibHTTPClient:
    """Default client. Stdlib-only."""

    user_agent = "jobhunt/0.1 (+https://example.com)"

    def get_json(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise HTTPClientError(f"{url} returned {resp.status}")
                body = resp.read()
        except urllib.error.URLError as exc:
            raise HTTPClientError(f"{url} failed: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPClientError(f"{url} returned non-JSON: {exc}") from exc

    def get_text(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> str:
        hdrs = {
            "User-Agent": self.user_agent,
            "Accept": "text/xml, application/rss+xml, text/plain",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise HTTPClientError(f"{url} returned {resp.status}")
                body = resp.read()
        except urllib.error.URLError as exc:
            raise HTTPClientError(f"{url} failed: {exc}") from exc
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return body.decode("latin-1")


class FakeHTTPClient:
    """Test double.

    Maps URL → JSON payload (or callable returning one) via ``routes``.
    Maps URL → text string (or callable returning one) via ``text_routes``.
    Both parameters are optional to keep backward compatibility.
    """

    def __init__(
        self,
        routes: dict[str, Any] | None = None,
        *,
        text_routes: dict[str, Any] | None = None,
    ) -> None:
        self._routes: dict[str, Any] = routes if routes is not None else {}
        self._text_routes: dict[str, Any] = text_routes if text_routes is not None else {}
        self.calls: list[str] = []

    def get_json(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append(url)
        if url not in self._routes:
            raise HTTPClientError(f"no fake route for {url}")
        value = self._routes[url]
        return value() if callable(value) else value

    def get_text(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> str:
        self.calls.append(url)
        if url not in self._text_routes:
            raise HTTPClientError(f"no fake text route for {url}")
        value = self._text_routes[url]
        return value() if callable(value) else value


class RateLimitedHTTPClient:
    """Wraps any :class:`HTTPClient` and enforces a :class:`~jobhunt.rate_limit.RateLimiter`.

    Before each request the limiter's :meth:`~jobhunt.rate_limit.RateLimiter.acquire`
    is called, which may block until a token is available.  All errors
    from the inner client propagate unchanged.

    Parameters
    ----------
    inner:
        The underlying HTTP client to delegate requests to.
    limiter:
        A :class:`~jobhunt.rate_limit.RateLimiter` instance that controls
        the request rate.
    """

    def __init__(self, inner: HTTPClient, limiter: "RateLimiter") -> None:
        self._inner = inner
        self._limiter = limiter

    def get_json(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self._limiter.acquire()
        return self._inner.get_json(url, timeout=timeout, headers=headers)

    def get_text(
        self, url: str, *, timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> str:
        self._limiter.acquire()
        return self._inner.get_text(url, timeout=timeout, headers=headers)
