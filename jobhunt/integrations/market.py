"""Salary intelligence + company news intel.

Salary uses Adzuna's histogram endpoint (reuses the ADZUNA_* keys already used
for discovery) to estimate a comp distribution for a role+location. Company news
uses NewsAPI (optional key) and reuses the existing NewsHeuristic for sentiment.
Both go through the injectable HTTPClient and are offline-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlencode

from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient

_CCY = {"us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD", "de": "EUR",
        "fr": "EUR", "in": "INR", "nl": "EUR", "sg": "SGD"}


@dataclass
class SalaryEstimate:
    role: str
    location: str
    currency: str
    p10: int
    median: int
    p90: int
    sample: int
    source: str = "adzuna"


class AdzunaSalaryClient:
    _BASE = "https://api.adzuna.com/v1/api/jobs/{country}/histogram?{qs}"

    def __init__(self, app_id: str, app_key: str, country: str = "us",
                 http: HTTPClient | None = None) -> None:
        self._id, self._key, self._country = app_id, app_key, country
        self._http = http or UrllibHTTPClient()

    def _url(self, role: str, location: str) -> str:
        params = [("app_id", self._id), ("app_key", self._key), ("what", role)]
        if location:
            params.append(("location0", location))
        return self._BASE.format(country=self._country, qs=urlencode(params))

    def estimate(self, role: str, location: str = "") -> SalaryEstimate:
        try:
            payload = self._http.get_json(self._url(role, location))
        except HTTPClientError as exc:
            raise RuntimeError(str(exc)) from exc
        hist = (payload.get("histogram") or {}) if isinstance(payload, dict) else {}
        bands = sorted((int(k), int(v)) for k, v in hist.items())
        total = sum(v for _, v in bands)

        def pct(p: float) -> int:
            if not bands:
                return 0
            target, cum = total * p, 0
            for sal, cnt in bands:
                cum += cnt
                if cum >= target:
                    return sal
            return bands[-1][0]

        return SalaryEstimate(
            role=role, location=location, currency=_CCY.get(self._country, ""),
            p10=pct(0.1), median=pct(0.5), p90=pct(0.9), sample=total)


def build_salary_client_from_env(http: HTTPClient | None = None) -> AdzunaSalaryClient | None:
    aid, akey = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (aid and akey):
        return None
    return AdzunaSalaryClient(aid, akey, os.environ.get("ADZUNA_COUNTRY", "us"), http)


@dataclass
class CompanyIntel:
    company: str
    sentiment: float
    headlines: list[dict] = field(default_factory=list)


class NewsClient:
    _BASE = "https://newsapi.org/v2/everything?{qs}"

    def __init__(self, api_key: str, http: HTTPClient | None = None) -> None:
        self._key = api_key
        self._http = http or UrllibHTTPClient()

    def company_intel(self, company: str) -> CompanyIntel:
        qs = urlencode([("q", company), ("apiKey", self._key),
                        ("pageSize", "5"), ("sortBy", "publishedAt"),
                        ("language", "en")])
        try:
            payload = self._http.get_json(self._BASE.format(qs=qs))
        except HTTPClientError as exc:
            raise RuntimeError(str(exc)) from exc
        arts = (payload.get("articles") or []) if isinstance(payload, dict) else []
        headlines = [{"title": a.get("title", ""), "url": a.get("url", ""),
                      "published_at": a.get("publishedAt", "")} for a in arts[:5]]
        # Reuse the existing news sentiment heuristic over the headline text.
        from jobhunt.enrichers.heuristic import NewsHeuristic
        text = " ".join(h["title"] for h in headlines)
        signals = NewsHeuristic().enrich(type("P", (), {"company": company, "jd_text": text})())
        sentiment = signals[0].value if signals else 0.5
        return CompanyIntel(company=company, sentiment=round(sentiment, 3),
                            headlines=headlines)


def build_news_client_from_env(http: HTTPClient | None = None) -> NewsClient | None:
    key = os.environ.get("JOBHUNT_NEWSAPI_KEY")
    return NewsClient(key, http) if key else None
