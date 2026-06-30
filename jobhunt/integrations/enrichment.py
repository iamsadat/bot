"""Recruiter / hiring-manager contact enrichment + outreach drafting.

Finds likely recruiter contacts at a target company (Hunter.io domain-search by
default; the ``ContactFinder`` protocol lets Apollo / People Data Labs plug in)
and drafts an **evidence-bound** outreach email — it references the candidate's
real matched skills and notes the real CV is attached, never fabricating claims.

All HTTP goes through the injectable ``HTTPClient`` so tests run offline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode, urlparse

logger = logging.getLogger(__name__)

from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient


@dataclass
class Contact:
    name: str
    email: str
    title: str = ""
    company: str = ""
    source: str = ""


class EnrichmentError(Exception):
    pass


class ContactFinder(Protocol):
    name: str
    def find(self, company: str, domain: str) -> list[Contact]: ...


def domain_from_url(url: str) -> str:
    """Best-effort registrable domain from a posting URL (for Hunter lookups)."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Strip common ATS subdomains so we query the employer's own domain.
    for ats in ("boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"):
        if host.endswith(ats):
            return ""  # ATS host, not the employer domain
    return host


class HunterContactFinder:
    name = "hunter"
    _BASE = "https://api.hunter.io/v2/domain-search?{qs}"

    def __init__(self, api_key: str, http: HTTPClient | None = None,
                 *, limit: int = 10) -> None:
        if not api_key:
            raise EnrichmentError("Hunter API key is required")
        self._key = api_key
        self._http = http or UrllibHTTPClient()
        self._limit = limit

    def find(self, company: str, domain: str) -> list[Contact]:
        if not domain:
            return []
        qs = urlencode([
            ("domain", domain), ("api_key", self._key),
            ("limit", str(self._limit)), ("department", "hr,executive,management"),
        ])
        try:
            payload = self._http.get_json(self._BASE.format(qs=qs))
        except HTTPClientError as exc:
            raise EnrichmentError(str(exc)) from exc
        emails = ((payload.get("data") or {}).get("emails", [])
                  if isinstance(payload, dict) else [])
        out: list[Contact] = []
        for e in emails:
            name = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x)
            out.append(Contact(
                name=name, email=e.get("value", ""), title=e.get("position", "") or "",
                company=company, source="hunter"))
        return [c for c in out if c.email]


def build_contact_finder_from_env(http: HTTPClient | None = None) -> ContactFinder | None:
    """Hunter by default; returns None when no provider key is configured."""
    if os.environ.get("JOBHUNT_HUNTER_API_KEY"):
        return HunterContactFinder(os.environ["JOBHUNT_HUNTER_API_KEY"], http)
    return None


def draft_outreach(profile, job: dict, doc: dict, contact: Contact,
                   *, llm=None) -> dict:
    """Draft an evidence-bound outreach email to ``contact``.

    References the candidate's real matched keywords from the tailored doc; never
    invents experience. Optional ``llm`` polishes tone (deterministic fallback).
    """
    name = getattr(profile, "name", "") if profile else ""
    company = job.get("company") or doc.get("company", "your team")
    title = job.get("title") or doc.get("title", "the role")
    matched = (doc.get("matched_keywords") or [])[:5]
    strengths = ", ".join(matched) if matched else "the core stack"
    first = (contact.name.split()[0] if contact.name else "there")
    subject = f"{name or 'Candidate'} — {title} at {company}"
    body = (
        f"Hi {first},\n\n"
        f"I've applied for the {title} role at {company} and wanted to reach out "
        f"directly. My background lines up closely with what you're hiring for — "
        f"particularly {strengths}. My résumé is attached.\n\n"
        f"Would you be open to a quick chat about the role?\n\n"
        f"Best,\n{name}"
    )
    if llm is not None:
        try:
            improved = llm("outreach", {
                "contact": contact.name, "company": company, "title": title,
                "strengths": matched, "draft": body})
            if improved and isinstance(improved, str):
                body = improved.strip()
        except Exception:
            logger.debug("LLM outreach polish failed, using template", exc_info=True)
    return {"to": contact.email, "subject": subject, "body": body,
            "contact": {"name": contact.name, "title": contact.title,
                        "email": contact.email}}
