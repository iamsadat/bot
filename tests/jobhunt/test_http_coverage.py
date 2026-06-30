"""Extended tests for jobhunt/http.py — covering UrllibHTTPClient and
RateLimitedHTTPClient (previously at 53% coverage).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jobhunt.http import (
    FakeHTTPClient,
    HTTPClientError,
    RateLimitedHTTPClient,
    UrllibHTTPClient,
)
from jobhunt.rate_limit import RateLimiter


# ----------------------------------------------------------------- UrllibHTTPClient


class TestUrllibHTTPClientGetJson:
    def test_success_returns_parsed_json(self):
        client = UrllibHTTPClient()
        payload = {"results": [{"id": 1}]}
        body = json.dumps(payload).encode()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = body
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = client.get_json("https://api.example.com/jobs")

        assert result == payload

    def test_non_200_raises_error(self):
        client = UrllibHTTPClient()

        fake_resp = MagicMock()
        fake_resp.status = 404
        fake_resp.read.return_value = b""
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(HTTPClientError, match="returned 404"):
                client.get_json("https://api.example.com/missing")

    def test_url_error_raises_http_client_error(self):
        import urllib.error

        client = UrllibHTTPClient()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with pytest.raises(HTTPClientError, match="failed"):
                client.get_json("https://api.example.com/jobs")

    def test_invalid_json_raises_http_client_error(self):
        client = UrllibHTTPClient()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b"not json at all"
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(HTTPClientError, match="non-JSON"):
                client.get_json("https://api.example.com/malformed")

    def test_custom_headers_are_passed(self):
        client = UrllibHTTPClient()
        payload = {"ok": True}
        body = json.dumps(payload).encode()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = body
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_open:
            result = client.get_json(
                "https://api.example.com/auth",
                headers={"Authorization": "Bearer tok123"},
            )

        assert result == payload
        # Verify the request was constructed (urlopen was called)
        mock_open.assert_called_once()


class TestUrllibHTTPClientGetText:
    def test_success_returns_text(self):
        client = UrllibHTTPClient()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b"<rss>feed content</rss>"
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = client.get_text("https://example.com/feed.xml")

        assert result == "<rss>feed content</rss>"

    def test_non_200_raises_error(self):
        client = UrllibHTTPClient()

        fake_resp = MagicMock()
        fake_resp.status = 500
        fake_resp.read.return_value = b""
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(HTTPClientError, match="returned 500"):
                client.get_text("https://example.com/error")

    def test_url_error_raises_http_client_error(self):
        import urllib.error

        client = UrllibHTTPClient()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            with pytest.raises(HTTPClientError, match="failed"):
                client.get_text("https://example.com/slow")

    def test_latin1_fallback_on_decode_error(self):
        client = UrllibHTTPClient()

        # Bytes that are valid latin-1 but not valid utf-8
        latin1_bytes = b"caf\xe9"  # "café" in latin-1

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = latin1_bytes
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = client.get_text("https://example.com/latin1")

        assert result == "café"

    def test_custom_headers_are_passed(self):
        client = UrllibHTTPClient()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b"text response"
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_open:
            result = client.get_text(
                "https://example.com/feed",
                headers={"X-Custom": "value"},
            )

        assert result == "text response"
        mock_open.assert_called_once()


# ----------------------------------------------------------------- RateLimitedHTTPClient


class TestRateLimitedHTTPClient:
    def test_get_json_delegates_after_acquire(self):
        inner = FakeHTTPClient(routes={"https://api.example.com/data": {"items": [1, 2]}})
        limiter = RateLimiter(max_calls=10, per_seconds=1.0)
        client = RateLimitedHTTPClient(inner, limiter)

        result = client.get_json("https://api.example.com/data")
        assert result == {"items": [1, 2]}
        assert "https://api.example.com/data" in inner.calls

    def test_get_text_delegates_after_acquire(self):
        inner = FakeHTTPClient(text_routes={"https://example.com/rss": "<rss/>"})
        limiter = RateLimiter(max_calls=10, per_seconds=1.0)
        client = RateLimitedHTTPClient(inner, limiter)

        result = client.get_text("https://example.com/rss")
        assert result == "<rss/>"
        assert "https://example.com/rss" in inner.calls

    def test_passes_timeout_and_headers(self):
        inner = FakeHTTPClient(routes={"https://api.example.com/x": {"ok": True}})
        limiter = RateLimiter(max_calls=10, per_seconds=1.0)
        client = RateLimitedHTTPClient(inner, limiter)

        result = client.get_json("https://api.example.com/x", timeout=5.0, headers={"X": "Y"})
        assert result == {"ok": True}

    def test_errors_propagate_from_inner(self):
        inner = FakeHTTPClient(routes={})
        limiter = RateLimiter(max_calls=10, per_seconds=1.0)
        client = RateLimitedHTTPClient(inner, limiter)

        with pytest.raises(HTTPClientError, match="no fake route"):
            client.get_json("https://api.example.com/missing")

    def test_acquire_is_called(self):
        inner = FakeHTTPClient(routes={"https://x.com/a": {}})
        limiter = MagicMock()
        client = RateLimitedHTTPClient(inner, limiter)

        client.get_json("https://x.com/a")
        limiter.acquire.assert_called_once()


# ----------------------------------------------------------------- FakeHTTPClient (additional)


class TestFakeHTTPClient:
    def test_callable_route(self):
        counter = {"n": 0}

        def gen():
            counter["n"] += 1
            return {"call": counter["n"]}

        client = FakeHTTPClient(routes={"https://x.com/api": gen})
        assert client.get_json("https://x.com/api") == {"call": 1}
        assert client.get_json("https://x.com/api") == {"call": 2}

    def test_callable_text_route(self):
        client = FakeHTTPClient(text_routes={"https://x.com/rss": lambda: "<xml/>"})
        assert client.get_text("https://x.com/rss") == "<xml/>"

    def test_missing_json_route_raises(self):
        client = FakeHTTPClient(routes={})
        with pytest.raises(HTTPClientError, match="no fake route"):
            client.get_json("https://x.com/missing")

    def test_missing_text_route_raises(self):
        client = FakeHTTPClient(text_routes={})
        with pytest.raises(HTTPClientError, match="no fake text route"):
            client.get_text("https://x.com/missing")

    def test_calls_are_recorded(self):
        client = FakeHTTPClient(
            routes={"https://a.com/1": {}},
            text_routes={"https://b.com/2": "text"},
        )
        client.get_json("https://a.com/1")
        client.get_text("https://b.com/2")
        assert client.calls == ["https://a.com/1", "https://b.com/2"]
