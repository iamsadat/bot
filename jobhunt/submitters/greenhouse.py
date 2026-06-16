"""Greenhouse job-board submitter.

Supports URLs of the form:
  - https://boards.greenhouse.io/<board_token>/jobs/<job_id>
  - https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs/<job_id>

See: https://developers.greenhouse.io/job-board.html#submitting-an-application
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from jobhunt.submitters.base import Poster, SubmitResult

if TYPE_CHECKING:
    pass

# Two URL forms we support:
#   1. https://boards.greenhouse.io/<token>/jobs/<job_id>         (public board)
#   2. https://boards-api.greenhouse.io/v1/boards/<token>/jobs/<job_id>  (API)
_BOARD_PUBLIC_RE = re.compile(
    r"boards\.greenhouse\.io/(?P<token>[^/]+)/jobs/(?P<job_id>[^/?#]+)",
    re.IGNORECASE,
)
_BOARD_API_RE = re.compile(
    r"boards-api\.greenhouse\.io(?:/v\d+)?/boards/(?P<token>[^/]+)/jobs/(?P<job_id>[^/?#]+)",
    re.IGNORECASE,
)

_API_ENDPOINT = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"


def _build_multipart(fields: dict[str, str | bytes]) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` payload from *fields*.

    Returns ``(body_bytes, content_type_header_value)``.
    Stdlib-only; no email.mime, just raw byte assembly.
    """
    boundary = f"----JobHuntBoundary{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        part_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"'
        )
        if name == "resume":
            part_header += '; filename="resume.pdf"'
            part_header += "\r\nContent-Type: application/pdf"
        part_header += "\r\n\r\n"

        if isinstance(value, str):
            value = value.encode()
        parts.append(part_header.encode() + value + b"\r\n")

    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


class GreenhouseSubmitter:
    """Posts an application to the Greenhouse Board API."""

    name = "greenhouse"

    def __init__(self, poster: Poster) -> None:
        self._poster = poster

    def supports(self, url: str) -> bool:
        return (
            "boards.greenhouse.io" in url.lower()
            or "boards-api.greenhouse.io" in url.lower()
        )

    def submit(self, plan: dict) -> SubmitResult:
        url = plan.get("url", "")
        m = _BOARD_PUBLIC_RE.search(url) or _BOARD_API_RE.search(url)
        if not m:
            return SubmitResult(ok=False, detail=f"could not parse board_token/job_id from URL: {url!r}")

        token = m.group("token")
        job_id = m.group("job_id")
        api_url = _API_ENDPOINT.format(token=token, job_id=job_id)

        applicant = plan.get("applicant", {})
        name_parts = applicant.get("name", "").split(None, 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        fields: dict[str, str | bytes] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": applicant.get("email", ""),
            "phone": applicant.get("phone", ""),
            "resume": plan.get("resume_text", "").encode(),
            "cover_letter": plan.get("cover_letter_text", ""),
        }

        body, content_type = _build_multipart(fields)
        headers = {"Content-Type": content_type}

        status, resp = self._poster.post_json(api_url, headers=headers, body=body)

        if 200 <= status < 300:
            return SubmitResult(
                ok=True,
                submission_id=str(resp.get("id", "")),
                detail="accepted",
            )
        return SubmitResult(ok=False, detail=str(status))
