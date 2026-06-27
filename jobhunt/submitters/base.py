"""Base protocols and utilities for submitters.

Defines a minimal POST surface (``Poster`` Protocol) and a ``Submitter``
Protocol so the registry can dispatch without depending on concrete
Greenhouse / Lever implementations.

``UrllibPoster`` is the real implementation; ``FakePoster`` is the test
double that records calls and returns configurable responses per URL.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ------------------------------------------------------------------ poster

@runtime_checkable
class Poster(Protocol):
    """Minimal POST surface.  Real impl wraps urllib; tests inject a fake."""

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | dict,
    ) -> tuple[int, dict]:
        """POST *body* to *url* and return ``(status_code, response_dict)``."""
        ...


class UrllibPoster:
    """Production poster — stdlib only, no third-party deps."""

    timeout: float = 15.0

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | dict,
    ) -> tuple[int, dict]:
        if isinstance(body, dict):
            payload = json.dumps(body).encode()
            headers = {"Content-Type": "application/json", **headers}
        else:
            payload = body

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            # Still return the status so callers can inspect it.
            try:
                raw = exc.read()
            except Exception:
                raw = b"{}"
            status = exc.code

        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, {}


class FakePoster:
    """Test double for ``Poster``.

    Pre-register responses as ``{url: (status, body_dict)}``.  All calls
    are recorded in ``self.calls`` as ``{"url": ..., "headers": ..., "body":
    ...}`` so tests can assert on the outgoing payload.
    """

    def __init__(self, responses: dict[str, tuple[int, dict]] | None = None) -> None:
        self._responses: dict[str, tuple[int, dict]] = responses or {}
        self.calls: list[dict[str, Any]] = []

    def add(self, url: str, status: int, body: dict) -> None:
        """Register a canned response for *url*."""
        self._responses[url] = (status, body)

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | dict,
    ) -> tuple[int, dict]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        if url not in self._responses:
            return (404, {"error": f"no fake route for {url}"})
        return self._responses[url]


# --------------------------------------------------------------- submit result

@dataclass
class SubmitResult:
    ok: bool
    submission_id: str = ""
    detail: str = ""


# --------------------------------------------------------------- submitter protocol

@runtime_checkable
class Submitter(Protocol):
    """Strategy for one ATS platform."""

    name: str

    def supports(self, url: str) -> bool:
        """Return True when this submitter can handle *url*."""
        ...

    def submit(self, plan: dict) -> SubmitResult:
        """Submit the application described by *plan*.

        *plan* keys:
          - ``url``               — job posting URL
          - ``job_id``            — canonical job identifier
          - ``applicant``         — dict with ``name``, ``email``, ``phone``
          - ``resume_text``       — plain-text resume body
          - ``cover_letter_text`` — cover letter body
        """
        ...
