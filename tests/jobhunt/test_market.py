"""Tests for salary intelligence + company news intel."""

from __future__ import annotations

import pytest

from jobhunt.http import FakeHTTPClient
from jobhunt.integrations.market import (
    AdzunaSalaryClient, NewsClient, build_news_client_from_env,
    build_salary_client_from_env,
)

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def test_salary_estimate_percentiles():
    client = AdzunaSalaryClient("id", "key", "us")
    url = client._url("backend engineer", "San Francisco")
    client._http = FakeHTTPClient({url: {"histogram": {
        "100000": 1, "150000": 4, "200000": 4, "250000": 1}}})
    est = client.estimate("backend engineer", "San Francisco")
    assert est.currency == "USD" and est.sample == 10
    assert est.p10 <= est.median <= est.p90
    assert est.median in (150000, 200000)


def test_salary_empty_histogram_is_safe():
    client = AdzunaSalaryClient("id", "key", "us")
    client._http = FakeHTTPClient({client._url("x", ""): {"histogram": {}}})
    est = client.estimate("x")
    assert est.sample == 0 and est.median == 0


def test_news_intel_parses_and_scores():
    client = NewsClient("KEY")
    from urllib.parse import urlencode
    url = NewsClient._BASE.format(qs=urlencode([
        ("q", "Acme"), ("apiKey", "KEY"), ("pageSize", "5"),
        ("sortBy", "publishedAt"), ("language", "en")]))
    client._http = FakeHTTPClient({url: {"articles": [
        {"title": "Acme raised a record funding round", "url": "u1",
         "publishedAt": "2026-06-01"},
        {"title": "Acme announced expansion", "url": "u2", "publishedAt": "2026-06-02"},
    ]}})
    intel = client.company_intel("Acme")
    assert len(intel.headlines) == 2 and intel.headlines[0]["url"] == "u1"
    assert isinstance(intel.sentiment, float)


def test_builders_env(monkeypatch):
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOBHUNT_NEWSAPI_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert build_salary_client_from_env() is None
    assert build_news_client_from_env() is None
    monkeypatch.setenv("ADZUNA_APP_ID", "a")
    monkeypatch.setenv("ADZUNA_APP_KEY", "b")
    monkeypatch.setenv("JOBHUNT_NEWSAPI_KEY", "n")
    assert build_salary_client_from_env() is not None
    assert build_news_client_from_env() is not None


def test_market_endpoints_gated_off(monkeypatch):
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOBHUNT_NEWSAPI_KEY"):
        monkeypatch.delenv(k, raising=False)
    st = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    c = TestClient(create_app(st))
    s = c.get("/api/market/status").json()
    assert s["salary"] is False and s["news"] is False
    assert c.get("/api/salary?role=backend").status_code == 400
    assert c.get("/api/company/intel?company=Acme").status_code == 400
