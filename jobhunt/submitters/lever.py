"""Lever job-board submitter.

Supports URLs of the form:
  - https://jobs.lever.co/<site>/<posting_id>

See: https://hire.lever.co/developer/postings#apply-to-a-posting
"""

from __future__ import annotations

import base64
import re

from jobhunt.submitters.base import Poster, SubmitResult

_LEVER_URL_RE = re.compile(
    r"jobs\.lever\.co/(?P<site>[^/]+)/(?P<posting_id>[^/?#]+)",
    re.IGNORECASE,
)

_API_ENDPOINT = "https://api.lever.co/v0/postings/{site}/{posting_id}/apply"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JobHunt/1.0"
)


class LeverSubmitter:
    """Posts an application via the Lever public Apply API."""

    name = "lever"

    def __init__(self, poster: Poster) -> None:
        self._poster = poster

    def supports(self, url: str) -> bool:
        return "jobs.lever.co" in url.lower()

    def submit(self, plan: dict) -> SubmitResult:
        url = plan.get("url", "")
        m = _LEVER_URL_RE.search(url)
        if not m:
            return SubmitResult(ok=False, detail=f"could not parse site/posting_id from URL: {url!r}")

        site = m.group("site")
        posting_id = m.group("posting_id")
        api_url = _API_ENDPOINT.format(site=site, posting_id=posting_id)

        applicant = plan.get("applicant", {})
        # Prefer a real rendered PDF; fall back to encoding the plain text.
        resume_bytes = plan.get("resume_pdf") or plan.get("resume_text", "").encode()
        resume_b64 = base64.b64encode(resume_bytes).decode()

        body = {
            "name": applicant.get("name", ""),
            "email": applicant.get("email", ""),
            "phone": applicant.get("phone", ""),
            "resume": resume_b64,
            "comments": plan.get("cover_letter_text", ""),
        }
        # Carry standard answers as best-effort extra fields (Lever ignores
        # unknown keys; known ones like urls/links are picked up).
        answers = plan.get("answers", {})
        for key in ("linkedin", "website", "github"):
            if answers.get(key):
                body.setdefault("urls", {})[key] = answers[key]

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": url,
        }
        status, resp = self._poster.post_json(api_url, headers=headers, body=body)

        if 200 <= status < 300:
            return SubmitResult(
                ok=True,
                submission_id=str(resp.get("id", "")),
                detail="accepted",
            )
        err = resp.get("error") or resp.get("errors") or ""
        return SubmitResult(ok=False, detail=f"{status}: {err}" if err else str(status))
