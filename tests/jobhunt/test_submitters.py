"""Tests for Phase-3 Greenhouse / Lever auto-submit (jobhunt.submitters).

All network calls are intercepted by FakePoster so the suite runs fully
offline.
"""

from __future__ import annotations

import base64
import json

import pytest

from jobhunt.agents.resume import TailoredDocument
from jobhunt.agents.submission import SubmissionAgent, SubmissionInputs, SubmissionPlan
from jobhunt.submitters import (
    FakePoster,
    GreenhouseSubmitter,
    LeverSubmitter,
    SubmitResult,
    SubmitterRegistry,
)


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def fake_poster():
    return FakePoster()


@pytest.fixture
def gh_poster():
    """FakePoster pre-loaded with a successful Greenhouse response."""
    p = FakePoster()
    p.add(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123",
        200,
        {"id": "GH-999"},
    )
    return p


@pytest.fixture
def lever_poster():
    """FakePoster pre-loaded with a successful Lever response."""
    p = FakePoster()
    p.add(
        "https://api.lever.co/v0/postings/acme/abc-def/apply",
        200,
        {"id": "LV-42"},
    )
    return p


def _make_doc(url: str, job_id: str = "j1", company: str = "Acme") -> TailoredDocument:
    return TailoredDocument(
        job_id=job_id,
        company=company,
        title="Backend Engineer",
        url=url,
        resume_text="Jane Doe\nPython, K8s",
        cover_letter_text="Dear Acme, ...",
        keyword_coverage=0.8,
        matched_keywords=["python", "kubernetes"],
        missing_keywords=[],
    )


def _make_inputs(doc: TailoredDocument, auto: bool, profile) -> SubmissionInputs:
    return SubmissionInputs(
        profile=profile,
        documents=[doc],
        auto_submit_approved=auto,
    )


# ========================================================== GreenhouseSubmitter

class TestGreenhouseSupports:
    def test_matches_boards_greenhouse_io(self):
        s = GreenhouseSubmitter(FakePoster())
        assert s.supports("https://boards.greenhouse.io/acme/jobs/123")

    def test_matches_boards_api_greenhouse_io(self):
        s = GreenhouseSubmitter(FakePoster())
        assert s.supports("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123")

    def test_does_not_match_lever(self):
        s = GreenhouseSubmitter(FakePoster())
        assert not s.supports("https://jobs.lever.co/acme/abc")

    def test_case_insensitive(self):
        s = GreenhouseSubmitter(FakePoster())
        assert s.supports("https://Boards.Greenhouse.IO/acme/jobs/99")


class TestGreenhouseSubmit:
    def test_happy_path_posts_to_correct_url(self, gh_poster):
        sub = GreenhouseSubmitter(gh_poster)
        plan = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "job_id": "123",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100"},
            "resume_text": "Jane's resume",
            "cover_letter_text": "Cover letter",
        }
        result = sub.submit(plan)
        assert result.ok is True
        assert result.submission_id == "GH-999"
        assert result.detail == "accepted"

    def test_happy_path_hits_correct_api_url(self, gh_poster):
        sub = GreenhouseSubmitter(gh_poster)
        plan = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "job_id": "123",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": ""},
            "resume_text": "resume",
            "cover_letter_text": "cover",
        }
        sub.submit(plan)
        assert len(gh_poster.calls) == 1
        assert gh_poster.calls[0]["url"] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"

    def test_multipart_body_contains_applicant_fields(self, gh_poster):
        sub = GreenhouseSubmitter(gh_poster)
        plan = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "job_id": "123",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100"},
            "resume_text": "Jane's resume",
            "cover_letter_text": "Cover",
        }
        sub.submit(plan)
        body: bytes = gh_poster.calls[0]["body"]
        body_str = body.decode(errors="replace")
        assert "Jane" in body_str
        assert "Doe" in body_str
        assert "jane@example.com" in body_str

    def test_non_2xx_returns_ok_false(self):
        p = FakePoster()
        p.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 422, {"error": "invalid"})
        sub = GreenhouseSubmitter(p)
        plan = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "job_id": "123",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": ""},
            "resume_text": "resume",
            "cover_letter_text": "cover",
        }
        result = sub.submit(plan)
        assert result.ok is False
        assert "422" in result.detail

    def test_unparseable_url_returns_ok_false(self, fake_poster):
        sub = GreenhouseSubmitter(fake_poster)
        result = sub.submit({"url": "https://example.com/no-match", "applicant": {}, "resume_text": "", "cover_letter_text": ""})
        assert result.ok is False
        assert "could not parse" in result.detail


# ============================================================= LeverSubmitter

class TestLeverSupports:
    def test_matches_jobs_lever_co(self):
        s = LeverSubmitter(FakePoster())
        assert s.supports("https://jobs.lever.co/acme/abc-def")

    def test_does_not_match_greenhouse(self):
        s = LeverSubmitter(FakePoster())
        assert not s.supports("https://boards.greenhouse.io/acme/jobs/1")


class TestLeverSubmit:
    def test_happy_path(self, lever_poster):
        sub = LeverSubmitter(lever_poster)
        plan = {
            "url": "https://jobs.lever.co/acme/abc-def",
            "job_id": "abc-def",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": "555"},
            "resume_text": "My resume",
            "cover_letter_text": "Hi there",
        }
        result = sub.submit(plan)
        assert result.ok is True
        assert result.submission_id == "LV-42"

    def test_resume_is_base64_encoded(self, lever_poster):
        sub = LeverSubmitter(lever_poster)
        plan = {
            "url": "https://jobs.lever.co/acme/abc-def",
            "job_id": "abc-def",
            "applicant": {"name": "Jane Doe", "email": "jane@example.com", "phone": ""},
            "resume_text": "My resume",
            "cover_letter_text": "",
        }
        sub.submit(plan)
        body: dict = lever_poster.calls[0]["body"]
        # The body is passed as a dict to post_json for Lever (JSON mode).
        assert base64.b64decode(body["resume"]) == b"My resume"

    def test_non_2xx_returns_ok_false(self):
        p = FakePoster()
        p.add("https://api.lever.co/v0/postings/acme/abc-def/apply", 500, {})
        sub = LeverSubmitter(p)
        result = sub.submit({
            "url": "https://jobs.lever.co/acme/abc-def",
            "job_id": "abc-def",
            "applicant": {"name": "X", "email": "x@x.com", "phone": ""},
            "resume_text": "",
            "cover_letter_text": "",
        })
        assert result.ok is False
        assert "500" in result.detail

    def test_unparseable_url_returns_ok_false(self):
        sub = LeverSubmitter(FakePoster())
        result = sub.submit({"url": "https://example.com/bad", "applicant": {}, "resume_text": "", "cover_letter_text": ""})
        assert result.ok is False


# ============================================================= SubmitterRegistry

class TestSubmitterRegistry:
    def test_for_url_picks_greenhouse(self, gh_poster):
        reg = SubmitterRegistry([GreenhouseSubmitter(gh_poster), LeverSubmitter(FakePoster())])
        sub = reg.for_url("https://boards.greenhouse.io/acme/jobs/123")
        assert sub is not None
        assert sub.name == "greenhouse"

    def test_for_url_picks_lever(self, lever_poster):
        reg = SubmitterRegistry([GreenhouseSubmitter(FakePoster()), LeverSubmitter(lever_poster)])
        sub = reg.for_url("https://jobs.lever.co/acme/abc")
        assert sub is not None
        assert sub.name == "lever"

    def test_for_url_returns_none_for_unknown(self):
        reg = SubmitterRegistry([GreenhouseSubmitter(FakePoster()), LeverSubmitter(FakePoster())])
        assert reg.for_url("https://workday.com/apply") is None

    def test_submit_returns_none_for_unknown_url(self):
        reg = SubmitterRegistry([GreenhouseSubmitter(FakePoster())])
        result = reg.submit({"url": "https://workday.com/apply"})
        assert result is None


# ============================================================= SubmissionAgent integration

class TestSubmissionAgentNoRegistry:
    """Without a registry, behaviour must be identical to the Phase-0 baseline."""

    def test_no_registry_produces_plan(self, profile, store, bus):
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.route == "api"
        assert plan.submitted is False
        assert plan.submission_id == ""

    def test_no_registry_requires_user_click_when_not_approved(self, profile, store, bus):
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus)
        res = agent.run(_make_inputs(doc, auto=False, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.requires_user_click is True


class TestSubmissionAgentWithRegistry:
    def _make_registry(self) -> tuple[SubmitterRegistry, FakePoster]:
        p = FakePoster()
        p.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 200, {"id": "GH-7"})
        reg = SubmitterRegistry([GreenhouseSubmitter(p)])
        return reg, p

    def test_auto_submit_approved_calls_submitter(self, profile, store, bus):
        reg, poster = self._make_registry()
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.submitted is True
        assert plan.submission_id == "GH-7"

    def test_auto_submit_false_does_not_call_submitter(self, profile, store, bus):
        reg, poster = self._make_registry()
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=False, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.submitted is False
        assert len(poster.calls) == 0

    def test_submission_id_recorded_on_plan(self, profile, store, bus):
        reg, _ = self._make_registry()
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.submission_id == "GH-7"
        assert "GH-7" in plan.notes

    def test_submitter_failure_sets_submitted_false(self, profile, store, bus):
        p = FakePoster()
        p.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 500, {})
        reg = SubmitterRegistry([GreenhouseSubmitter(p)])
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.submitted is False
        assert "auto-submit failed" in plan.notes

    def test_non_api_route_skips_submitter(self, profile, store, bus):
        """Even with registry + auto_submit_approved, non-API routes are skipped."""
        p = FakePoster()
        reg = SubmitterRegistry([GreenhouseSubmitter(p)])
        doc = _make_doc("https://myworkdayjobs.com/acme/job/1")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        [plan] = res.output
        assert plan.route == "autofill"
        assert plan.submitted is False
        assert len(p.calls) == 0

    def test_critique_includes_submission_success_metric(self, profile, store, bus):
        reg, _ = self._make_registry()
        doc = _make_doc("https://boards.greenhouse.io/acme/jobs/123")
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(_make_inputs(doc, auto=True, profile=profile), task_id="t")
        assert "submission_success" in res.trace.self_critique
        assert res.trace.self_critique["submission_success"] == 1.0

    def test_manual_route_not_counted_in_submission_success(self, profile, store, bus):
        """Manual-route plans shouldn't dilute the submission_success metric."""
        p = FakePoster()
        p.add("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", 200, {"id": "X"})
        reg = SubmitterRegistry([GreenhouseSubmitter(p)])
        doc_api = _make_doc("https://boards.greenhouse.io/acme/jobs/123", job_id="j1")
        doc_manual = _make_doc("https://unknownboard.example.com/job/999", job_id="j2")
        inputs = SubmissionInputs(
            profile=profile,
            documents=[doc_api, doc_manual],
            auto_submit_approved=True,
        )
        agent = SubmissionAgent(store, bus, registry=reg)
        res = agent.run(inputs, task_id="t")
        # Only the api-routed plan counts; success = 1/1 = 1.0
        assert res.trace.self_critique["submission_success"] == 1.0
