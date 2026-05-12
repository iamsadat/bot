"""Minimal HTTP client used by job-board adapters.

We keep this stdlib-only so the package has no production dependency
beyond Python itself. The class form is what matters: tests inject a
``FakeHTTPClient`` that returns canned JSON, so adapter logic is
exercised offline.

Real adapters call ``client.get_json(url)``. The client enforces a
timeout, parses JSON, and surfaces transport failures as
``HTTPClientError`` so the agent-level :func:`jobhunt.tools.call_tool`
wrapper can translate them into degraded results.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class HTTPClientError(Exception):
    """Transport-level error from a job-board API."""


class HTTPClient(Protocol):
    def get_json(self, url: str, *, timeout: float = 10.0) -> Any: ...


class UrllibHTTPClient:
    """Default client. Stdlib-only."""

    user_agent = "jobhunt/0.1 (+https://example.com)"

    def get_json(self, url: str, *, timeout: float = 10.0) -> Any:
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
        )
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


class FakeHTTPClient:
    """Test double. Maps URL → JSON payload (or callable returning one)."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    def get_json(self, url: str, *, timeout: float = 10.0) -> Any:
        self.calls.append(url)
        if url not in self._routes:
            raise HTTPClientError(f"no fake route for {url}")
        value = self._routes[url]
        return value() if callable(value) else value
