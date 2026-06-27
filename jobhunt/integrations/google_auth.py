"""OAuth2 token handling and injectable HTTP transport for Google APIs.

Provides:
- ``GoogleCredentials``  — OAuth2 credential bundle
- ``TokenProvider``      — Protocol for getting a valid access token
- ``StaticTokenProvider``— fixed-token test double
- ``OAuthTokenProvider`` — refreshes via the Google token endpoint over an
                           injectable ``Transport``
- ``Transport``          — Protocol for making raw HTTP requests
- ``FakeTransport``      — offline test double, routes keyed by
                           ``(method, url-substring)``
- ``GoogleAPIError``     — raised on unmapped routes or HTTP >= 400

No third-party Google client libraries are used — everything is hand-rolled
stdlib REST calls so the whole stack stays unit-testable offline, mirroring
``jobhunt.http.FakeHTTPClient`` and ``jobhunt.submitters.base.FakePoster``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class GoogleAPIError(Exception):
    """Raised on an unmapped fake route or an HTTP status >= 400."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass
class GoogleCredentials:
    """OAuth2 credential bundle for a single Google account."""

    access_token: str
    refresh_token: str
    expiry_epoch: float                  # unix timestamp the access_token expires
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

@runtime_checkable
class Transport(Protocol):
    """Minimal HTTP surface used by all Google REST calls."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict | bytes | None = None,
    ) -> tuple[int, dict]:
        """Send a request and return ``(status_code, response_json)``."""
        ...


class UrllibTransport:
    """Production transport — stdlib only, no third-party deps."""

    timeout: float = 15.0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict | bytes | None = None,
    ) -> tuple[int, dict]:
        headers = dict(headers or {})
        payload: bytes | None
        if isinstance(body, dict):
            payload = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        else:
            payload = body

        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            except Exception:
                raw = b"{}"
            status = exc.code
        except urllib.error.URLError as exc:
            raise GoogleAPIError(f"{url} failed: {exc}") from exc

        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return status, parsed


class FakeTransport:
    """Offline test double for :class:`Transport`.

    Routes are registered as ``{(method, url_substring): response}`` where
    ``response`` is either ``(status, json_dict)`` or a zero-arg callable
    returning that tuple (for dynamic / stateful responses).

    ``url_substring`` only needs to be contained within the requested URL,
    so callers don't have to spell out query strings exactly.

    Every call is recorded — in order — in ``self.calls`` as a dict with
    ``method``, ``url``, ``headers``, ``body`` keys, so tests can assert on
    the exact outgoing call sequence.

    Raises :class:`GoogleAPIError` when no route matches, or when the
    matched response status is >= 400.
    """

    def __init__(
        self,
        routes: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self._routes: dict[tuple[str, str], Any] = dict(routes or {})
        self.calls: list[dict[str, Any]] = []

    def add(self, method: str, url_substring: str, status: int, body: dict) -> None:
        """Register a canned response for ``(method, url_substring)``."""
        self._routes[(method.upper(), url_substring)] = (status, body)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict | bytes | None = None,
    ) -> tuple[int, dict]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "body": body}
        )

        method_upper = method.upper()
        for (route_method, substring), response in self._routes.items():
            if route_method == method_upper and substring in url:
                status, payload = response() if callable(response) else response
                if status >= 400:
                    raise GoogleAPIError(
                        f"{method} {url} returned {status}: {payload}"
                    )
                return status, payload

        raise GoogleAPIError(f"no fake route for {method} {url}")


# ---------------------------------------------------------------------------
# Token providers
# ---------------------------------------------------------------------------

@runtime_checkable
class TokenProvider(Protocol):
    """Strategy for obtaining a valid OAuth2 access token."""

    def get_access_token(self) -> str:
        """Return a valid (refreshed if necessary) access token."""
        ...


class StaticTokenProvider:
    """Returns a fixed token. For tests / static service-account style use."""

    def __init__(self, token: str = "fake-access-token") -> None:
        self._token = token

    def get_access_token(self) -> str:
        return self._token


class OAuthTokenProvider:
    """Refreshes an access token via Google's OAuth2 token endpoint.

    The token endpoint is reached through an injectable :class:`Transport`
    (no real network access, no Google client libs).  A ``clock`` callable
    (defaults to ``time.time``) is injectable so expiry-driven refresh logic
    is deterministic in tests.

    The cached access token is reused while it has not yet expired (with a
    small safety margin); once expired (or on the very first call) a refresh
    POST is issued and the resulting token + expiry are cached.
    """

    #: seconds of safety margin subtracted from the expiry before triggering
    #: a refresh, so a token that is about to expire isn't handed out.
    EXPIRY_MARGIN = 30.0

    def __init__(
        self,
        credentials: GoogleCredentials,
        transport: Transport,
        *,
        clock: Callable[[], float] | None = None,
        token_endpoint: str = TOKEN_ENDPOINT,
    ) -> None:
        self._creds = credentials
        self._transport = transport
        self._clock = clock if clock is not None else time.time
        self._token_endpoint = token_endpoint

    def get_access_token(self) -> str:
        if self._is_expired():
            self._refresh()
        return self._creds.access_token

    # ------------------------------------------------------------------

    def _is_expired(self) -> bool:
        now = self._clock()
        return now >= (self._creds.expiry_epoch - self.EXPIRY_MARGIN)

    def _refresh(self) -> None:
        body = {
            "client_id": self._creds.client_id,
            "client_secret": self._creds.client_secret,
            "refresh_token": self._creds.refresh_token,
            "grant_type": "refresh_token",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        status, payload = self._transport.request(
            "POST", self._token_endpoint, headers=headers, body=body
        )
        if status >= 400:
            raise GoogleAPIError(
                f"token refresh failed with status {status}: {payload}"
            )
        access_token = payload.get("access_token")
        if not access_token:
            raise GoogleAPIError(f"token refresh response missing access_token: {payload}")

        expires_in = payload.get("expires_in", 3600)
        self._creds.access_token = access_token
        self._creds.expiry_epoch = self._clock() + float(expires_in)
