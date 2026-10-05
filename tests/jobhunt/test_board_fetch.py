"""Fetching many company boards: concurrency and failure isolation."""

from __future__ import annotations

import urllib.request

import pytest

from jobhunt.adapters import GreenhouseSource
from jobhunt.adapters.base import fetch_boards
from jobhunt.http import FakeHTTPClient, HTTPClientError, UrllibHTTPClient


def test_fetch_boards_keeps_input_order_and_isolates_failures():
    http = FakeHTTPClient({f"https://x/{k}": {"k": k} for k in ("a", "c")})
    out = fetch_boards(http, {k: f"https://x/{k}" for k in ("a", "b", "c")})
    assert [k for k, _, _ in out] == ["a", "b", "c"]
    assert out[0][1] == {"k": "a"} and out[2][1] == {"k": "c"}
    assert out[1][1] is None and out[1][2].startswith("b:")


def test_one_dead_board_does_not_take_out_the_source():
    http = FakeHTTPClient({
        "https://boards-api.greenhouse.io/v1/boards/live/jobs?content=true": {
            "jobs": [{"id": 1, "title": "Data Engineer", "location": {"name": "Hyderabad"},
                      "absolute_url": "https://job-boards.greenhouse.io/live/jobs/1",
                      "content": "", "updated_at": "2026-10-01T00:00:00Z"}]},
    })
    postings = GreenhouseSource(["dead", "live"], http=http)._fetch_all()
    assert [p.title for p in postings] == ["Data Engineer"]


@pytest.mark.parametrize("exc", [TimeoutError("read timed out"),
                                 ConnectionResetError("reset by peer")])
def test_transport_errors_mid_read_become_http_client_errors(monkeypatch, exc):
    """A read timeout is not a URLError, so it used to escape the per-board
    failure handling and abort a whole sweep."""
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(HTTPClientError):
        UrllibHTTPClient().get_json("https://example.com/x")
