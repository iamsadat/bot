"""Greenhouse job-board submitter.

Supports URLs of the form:
  - https://boards.greenhouse.io/<board_token>/jobs/<job_id>
  - https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs/<job_id>

See: https://developers.greenhouse.io/job-board.html#submitting-an-application
"""

from __future__ import annotations

import json
import re
import urllib.request
import uuid
from collections.abc import Callable

from jobhunt.submitters.base import Poster, SubmitResult

# A browser-like UA + Accept reduce the chance the public board endpoints
# reject our request as non-browser automation.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JobHunt/1.0"
    ),
    "Accept": "application/json, text/plain, */*",
}

_QUESTIONS_ENDPOINT = (
    "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"
)

# Fields handled directly from the applicant/résumé — never overwritten by the
# custom-question mapper.
_BASE_FIELDS = {
    "first_name", "last_name", "email", "phone", "full_name", "name",
    "resume", "resume_text", "cover_letter", "cover_letter_text",
}


def _default_question_fetcher(url: str) -> dict:
    req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _answer_for(label: str, answers: dict, applicant: dict) -> str:
    """Best-effort map a question label to a stored standard answer."""
    label_l = label.lower()

    def pick(*keys: str) -> str:
        for k in keys:
            v = answers.get(k)
            if v not in (None, ""):
                return str(v)
        return ""

    if "sponsor" in label_l:
        return pick("requires_sponsorship", "sponsorship")
    if any(k in label_l for k in ("authoriz", "right to work", "legally", "eligible to work")):
        return pick("work_authorization", "authorized")
    if "years" in label_l and ("experience" in label_l or "exp" in label_l):
        return pick("years_experience")
    if "linkedin" in label_l:
        return pick("linkedin")
    if any(k in label_l for k in ("website", "portfolio", "github")):
        return pick("website", "portfolio")
    if any(k in label_l for k in ("salary", "compensation", "pay")):
        return pick("salary_expectation", "desired_salary")
    if "gender" in label_l:
        return pick("gender")
    if "race" in label_l or "ethnic" in label_l:
        return pick("race", "ethnicity")
    if "veteran" in label_l:
        return pick("veteran_status")
    if "disab" in label_l:
        return pick("disability_status")
    if any(k in label_l for k in ("location", "based", "city", "where are you")):
        return applicant.get("location", "") or pick("location")
    # Generic yes/no screeners — a stored default lets the user opt in.
    return pick("default_yes_no")


def _resolve_value(field: dict, answer: str) -> str | None:
    """Resolve a textual answer against a select field's allowed values."""
    if not answer:
        return None
    values = field.get("values")
    if not values:
        return answer  # free-text field
    ans = answer.strip().lower()
    for v in values:
        if str(v.get("label", "")).strip().lower() == ans:
            return str(v.get("value"))
    for v in values:  # looser contains match
        if ans in str(v.get("label", "")).strip().lower():
            return str(v.get("value"))
    return None


def map_questions(questions: list, answers: dict, applicant: dict) -> dict:
    """Map a Greenhouse ``questions`` payload to ``{field_name: value}``."""
    out: dict[str, str] = {}
    for q in questions or []:
        label = q.get("label", "")
        for f in q.get("fields", []):
            name = f.get("name", "")
            if not name or name in _BASE_FIELDS:
                continue
            resolved = _resolve_value(f, _answer_for(label, answers, applicant))
            if resolved not in (None, ""):
                out[name] = str(resolved)
    return out

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
    """Posts an application to the Greenhouse Board API.

    Sends a real rendered PDF (``plan['resume_pdf']`` when present), browser-like
    headers, and best-effort answers to the board's custom screening questions
    (fetched live and mapped from ``plan['answers']``).
    """

    name = "greenhouse"

    def __init__(
        self,
        poster: Poster,
        question_fetcher: Callable[[str], dict] | None = None,
    ) -> None:
        self._poster = poster
        # When None, custom-question fetching is skipped entirely (keeps tests
        # offline). The live dashboard passes the real urllib fetcher
        # (``_default_question_fetcher``).
        self._fetch_questions = question_fetcher

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

        # Prefer a real rendered PDF; fall back to encoding the plain text.
        resume_bytes = plan.get("resume_pdf") or plan.get("resume_text", "").encode()
        fields: dict[str, str | bytes] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": applicant.get("email", ""),
            "phone": applicant.get("phone", ""),
            "resume": resume_bytes,
            "cover_letter": plan.get("cover_letter_text", ""),
        }

        # Answer the board's custom screening questions where we can.
        unanswered: list[str] = []
        try:
            if self._fetch_questions is None:
                raise RuntimeError("question fetching disabled")
            payload = self._fetch_questions(
                _QUESTIONS_ENDPOINT.format(token=token, job_id=job_id)
            )
            questions = payload.get("questions", [])
            mapped = map_questions(questions, plan.get("answers", {}), applicant)
            fields.update(mapped)
            for q in questions:
                if not q.get("required"):
                    continue
                names = [f.get("name", "") for f in q.get("fields", [])]
                if not any(n in fields and fields[n] not in ("", b"") for n in names):
                    unanswered.append(q.get("label", "?"))
        except Exception:
            # No question data (offline/unsupported) — submit base fields and
            # let the API report any missing required questions.
            pass

        body, content_type = _build_multipart(fields)
        headers = {"Content-Type": content_type, "Referer": url, **_BROWSER_HEADERS}

        status, resp = self._poster.post_json(api_url, headers=headers, body=body)

        if 200 <= status < 300:
            detail = "accepted"
            if unanswered:
                detail += f"; unanswered required: {', '.join(unanswered)}"
            return SubmitResult(ok=True, submission_id=str(resp.get("id", "")), detail=detail)

        err = resp.get("error") or resp.get("errors") or ""
        detail = f"{status}: {err}" if err else str(status)
        if unanswered:
            detail += f" (unanswered required questions: {', '.join(unanswered)})"
        return SubmitResult(ok=False, detail=detail)
