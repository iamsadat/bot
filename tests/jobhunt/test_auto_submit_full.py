"""Tests for full real auto-submit: real PDF upload, browser headers, and
Greenhouse custom-question answering."""

from __future__ import annotations

from jobhunt.submitters.base import FakePoster
from jobhunt.submitters.greenhouse import GreenhouseSubmitter, map_questions
from jobhunt.submitters.lever import LeverSubmitter

_GH_URL = "https://boards.greenhouse.io/acme/jobs/123"
_GH_API = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"

_QUESTIONS = [
    {"label": "Resume", "required": True,
     "fields": [{"name": "resume", "type": "input_file"}]},
    {"label": "Are you legally authorized to work in the US?", "required": True,
     "fields": [{"name": "q_auth", "type": "multi_value_single_select",
                 "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
    {"label": "Will you now or in the future require sponsorship?", "required": True,
     "fields": [{"name": "q_spon", "type": "multi_value_single_select",
                 "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
    {"label": "LinkedIn Profile", "required": False,
     "fields": [{"name": "q_li", "type": "input_text"}]},
]

_ANSWERS = {"work_authorization": "Yes", "requires_sponsorship": "No",
            "linkedin": "https://linkedin.com/in/ada"}


def test_map_questions_resolves_selects_and_text():
    out = map_questions(_QUESTIONS, _ANSWERS, {"name": "Ada"})
    assert out == {"q_auth": "1", "q_spon": "0", "q_li": "https://linkedin.com/in/ada"}
    assert "resume" not in out  # base field never overwritten


def test_greenhouse_uploads_real_pdf_and_answers_questions():
    poster = FakePoster()
    poster.add(_GH_API, 201, {"id": "GH-9"})
    sub = GreenhouseSubmitter(poster, question_fetcher=lambda url: {"questions": _QUESTIONS})
    res = sub.submit({
        "url": _GH_URL, "job_id": "123",
        "applicant": {"name": "Ada Lovelace", "email": "a@x.com", "phone": "+1555"},
        "resume_pdf": b"%PDF-1.4 real", "cover_letter_text": "Dear Acme",
        "answers": _ANSWERS,
    })
    assert res.ok and res.submission_id == "GH-9"
    body = poster.calls[0]["body"]
    assert b"%PDF-1.4 real" in body                 # the actual PDF, not text
    assert b'name="q_auth"' in body and b'name="q_spon"' in body
    assert "User-Agent" in poster.calls[0]["headers"]  # browser header


def test_greenhouse_skips_fetch_when_no_fetcher():
    """Default (no fetcher) → no network, submits base fields only."""
    poster = FakePoster()
    poster.add(_GH_API, 200, {"id": "GH-1"})
    sub = GreenhouseSubmitter(poster)  # fetcher None
    res = sub.submit({
        "url": _GH_URL, "job_id": "123",
        "applicant": {"name": "Ada", "email": "a@x.com"},
        "resume_text": "Ada", "answers": _ANSWERS,
    })
    assert res.ok
    assert len(poster.calls) == 1  # only the POST, no GET


def test_greenhouse_falls_back_to_text_without_pdf():
    poster = FakePoster()
    poster.add(_GH_API, 201, {"id": "GH-2"})
    sub = GreenhouseSubmitter(poster)
    res = sub.submit({
        "url": _GH_URL, "job_id": "123",
        "applicant": {"name": "Ada", "email": "a@x.com"},
        "resume_text": "Ada Lovelace plain text",
    })
    assert res.ok
    assert b"Ada Lovelace plain text" in poster.calls[0]["body"]


def test_greenhouse_error_detail_includes_api_message():
    poster = FakePoster()
    poster.add(_GH_API, 422, {"error": "missing required field"})
    sub = GreenhouseSubmitter(poster)
    res = sub.submit({
        "url": _GH_URL, "job_id": "123",
        "applicant": {"name": "Ada", "email": "a@x.com"}, "resume_text": "x",
    })
    assert res.ok is False and "missing required field" in res.detail


def test_lever_prefers_pdf_and_sends_headers():
    poster = FakePoster()
    api = "https://api.lever.co/v0/postings/netflix/abc/apply"
    poster.add(api, 200, {"id": "LV-3"})
    sub = LeverSubmitter(poster)
    res = sub.submit({
        "url": "https://jobs.lever.co/netflix/abc", "job_id": "abc",
        "applicant": {"name": "Ada", "email": "a@x.com"},
        "resume_pdf": b"%PDF-1.4 lever", "answers": {"linkedin": "https://lnkd/ada"},
    })
    assert res.ok and res.submission_id == "LV-3"
    import base64
    sent_resume = base64.b64decode(poster.calls[0]["body"]["resume"])
    assert sent_resume == b"%PDF-1.4 lever"
    assert poster.calls[0]["headers"].get("User-Agent")
