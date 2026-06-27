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
        resume_bytes = plan.get("resume_text", "").encode()
        resume_b64 = base64.b64encode(resume_bytes).decode()

        body = {
            "name": applicant.get("name", ""),
            "email": applicant.get("email", ""),
            "phone": applicant.get("phone", ""),
            "resume": resume_b64,
            "comments": plan.get("cover_letter_text", ""),
        }

        headers = {"Content-Type": "application/json"}
        status, resp = self._poster.post_json(api_url, headers=headers, body=body)

        if 200 <= status < 300:
            return SubmitResult(
                ok=True,
                submission_id=str(resp.get("id", "")),
                detail="accepted",
            )
        return SubmitResult(ok=False, detail=str(status))
