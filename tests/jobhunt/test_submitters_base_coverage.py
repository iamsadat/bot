"""Extended tests for jobhunt/submitters/base.py — covering UrllibPoster
(previously at 62% coverage).
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from jobhunt.submitters.base import (
    FakePoster,
    Poster,
    SubmitResult,
    UrllibPoster,
)


# ----------------------------------------------------------------- UrllibPoster


class TestUrllibPoster:
    def test_post_json_with_dict_body(self):
        poster = UrllibPoster()
        response_body = json.dumps({"id": "sub-123", "status": "ok"}).encode()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = response_body
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            status, data = poster.post_json(
                "https://api.greenhouse.io/v1/applications",
                headers={"Authorization": "Basic abc123"},
                body={"first_name": "Jane", "last_name": "Doe"},
            )

        assert status == 200
        assert data == {"id": "sub-123", "status": "ok"}

    def test_post_json_with_bytes_body(self):
        poster = UrllibPoster()
        response_body = json.dumps({"ok": True}).encode()

        fake_resp = MagicMock()
        fake_resp.status = 201
        fake_resp.read.return_value = response_body
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            status, data = poster.post_json(
                "https://api.lever.co/v0/postings/apply",
                headers={"Content-Type": "multipart/form-data"},
                body=b"raw binary data",
            )

        assert status == 201
        assert data == {"ok": True}

    def test_http_error_returns_status_and_body(self):
        poster = UrllibPoster()
        error_body = json.dumps({"error": "invalid_application"}).encode()

        exc = urllib.error.HTTPError(
            "https://api.greenhouse.io/v1/applications",
            422,
            "Unprocessable Entity",
            {},
            None,
        )
        exc.read = MagicMock(return_value=error_body)

        with patch("urllib.request.urlopen", side_effect=exc):
            status, data = poster.post_json(
                "https://api.greenhouse.io/v1/applications",
                headers={"Authorization": "Basic abc"},
                body={"name": "test"},
            )

        assert status == 422
        assert data == {"error": "invalid_application"}

    def test_http_error_with_unreadable_body(self):
        poster = UrllibPoster()

        exc = urllib.error.HTTPError(
            "https://api.example.com/apply",
            500,
            "Internal Server Error",
            {},
            None,
        )
        exc.read = MagicMock(side_effect=Exception("stream closed"))

        with patch("urllib.request.urlopen", side_effect=exc):
            status, data = poster.post_json(
                "https://api.example.com/apply",
                headers={},
                body={"x": 1},
            )

        assert status == 500
        assert data == {}

    def test_non_json_response_returns_empty_dict(self):
        poster = UrllibPoster()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b"OK"  # not JSON
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            status, data = poster.post_json(
                "https://api.example.com/submit",
                headers={},
                body={"app": "data"},
            )

        assert status == 200
        assert data == {}

    def test_dict_body_sets_content_type(self):
        poster = UrllibPoster()
        response_body = json.dumps({}).encode()

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = response_body
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_open:
            poster.post_json(
                "https://api.example.com/x",
                headers={"Authorization": "Bearer tok"},
                body={"key": "value"},
            )

        # The request should have been created with Content-Type header
        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        assert req.get_header("Content-type") == "application/json"

    def test_timeout_property(self):
        poster = UrllibPoster()
        assert poster.timeout == 15.0


# ----------------------------------------------------------------- FakePoster


class TestFakePoster:
    def test_registered_response(self):
        poster = FakePoster({"https://x.com/apply": (200, {"id": "abc"})})
        status, data = poster.post_json(
            "https://x.com/apply", headers={"H": "V"}, body={"k": "v"}
        )
        assert status == 200
        assert data == {"id": "abc"}

    def test_unregistered_url_returns_404(self):
        poster = FakePoster()
        status, data = poster.post_json(
            "https://x.com/unknown", headers={}, body={}
        )
        assert status == 404
        assert "error" in data

    def test_calls_are_recorded(self):
        poster = FakePoster({"https://x.com/a": (201, {})})
        poster.post_json("https://x.com/a", headers={"A": "B"}, body={"x": 1})
        assert len(poster.calls) == 1
        assert poster.calls[0]["url"] == "https://x.com/a"
        assert poster.calls[0]["headers"] == {"A": "B"}
        assert poster.calls[0]["body"] == {"x": 1}

    def test_add_method(self):
        poster = FakePoster()
        poster.add("https://x.com/new", 200, {"added": True})
        status, data = poster.post_json(
            "https://x.com/new", headers={}, body={}
        )
        assert status == 200
        assert data == {"added": True}


# ----------------------------------------------------------------- SubmitResult


class TestSubmitResult:
    def test_default_values(self):
        r = SubmitResult(ok=True)
        assert r.ok is True
        assert r.submission_id == ""
        assert r.detail == ""

    def test_with_all_fields(self):
        r = SubmitResult(ok=True, submission_id="sub-123", detail="Application received")
        assert r.submission_id == "sub-123"
        assert r.detail == "Application received"

    def test_failure_result(self):
        r = SubmitResult(ok=False, detail="Invalid resume format")
        assert r.ok is False
        assert r.detail == "Invalid resume format"


# ----------------------------------------------------------------- Protocol checks


class TestProtocols:
    def test_fake_poster_satisfies_poster_protocol(self):
        poster = FakePoster()
        assert isinstance(poster, Poster)

    def test_urllib_poster_satisfies_poster_protocol(self):
        poster = UrllibPoster()
        assert isinstance(poster, Poster)
