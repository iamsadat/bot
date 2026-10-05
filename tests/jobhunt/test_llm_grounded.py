"""Grounded LLM generation: cover letter, interview prep, recruiter outreach.

Every path runs offline through ``FakeLLMClient`` and checks three things:
(a) grounded LLM output is used, (b) empty output or an LLM error falls back
to the deterministic text unchanged, (c) the hallucination post-check (an
invented skill or number) also falls back.
"""

from __future__ import annotations

import json

import pytest

from jobhunt.agents.resume import ResumeArchitectAgent, ResumeInputs
from jobhunt.integrations.enrichment import Contact, draft_outreach
from jobhunt.interview import answer_feedback, generate_questions
from jobhunt.llm import FakeLLMClient, LLMError, resume_callback
from jobhunt.llm.grounding import grounding_violations, profile_fact_sheet
from jobhunt.models import JobPosting, UserProfile

# ---------------------------------------------------------------- fixtures

GROUNDED_LETTER = """\
Dear Hiring Team at Acme,

I am writing to apply for the Backend Engineer role at Acme. As a Senior \
Backend Engineer, I have built and operated Python services on Kubernetes, \
which maps closely to the platform work described in your job description. \
I enjoy owning services end to end, from the first design discussion through \
deployment, monitoring and the steady improvements that follow a launch.

Your role asks for strong Python, Kubernetes and PostgreSQL skills. I work \
with all three every day, along with Redis and FastAPI, and I care about \
observability so that problems are visible before customers notice them. I \
would bring the same careful, reliable approach to Acme and its users.

I would welcome the opportunity to discuss how my experience with Python \
services on Kubernetes could support your team's goals. Thank you for your \
time and consideration, and I look forward to hearing from you at your \
convenience.

Regards,
Test User"""


@pytest.fixture
def posting() -> JobPosting:
    return JobPosting(
        job_id="j1", source="g", source_id="1",
        url="https://boards.greenhouse.io/acme/j1",
        title="Backend Engineer", company="Acme", location="Bengaluru",
        jd_text="We need strong Python, Kubernetes and PostgreSQL skills.",
        remote=True,
    )


def _agent(store, bus, client):
    return ResumeArchitectAgent(store, bus, llm=resume_callback(client))


def _cover(store, bus, profile, posting, client) -> str:
    res = _agent(store, bus, client).run(
        ResumeInputs(profile=profile, postings=[posting]), task_id="t")
    return res.output[0].cover_letter_text


def _only(action_word: str, response, calls: list | None = None):
    """Responder answering only the prompt whose system text has ``action_word``."""
    def responder(system: str, user: str) -> str:
        if action_word not in system.lower():
            return ""
        if calls is not None:
            calls.append((system, user))
        if isinstance(response, Exception):
            raise response
        return response
    return FakeLLMClient(responder)


# ------------------------------------------------------------ grounding unit

def test_fact_sheet_excludes_contact_details(profile):
    profile.phone = "+91 98765 43210"
    profile.links = {"linkedin": "https://linkedin.com/in/test"}
    sheet = profile_fact_sheet(profile.to_dict())
    assert "Test User" in sheet and "Python services on Kubernetes" in sheet
    assert "test@example.com" not in sheet
    assert "98765" not in sheet and "linkedin" not in sheet.lower()


def test_grounding_violations_flags_invented_skill_and_number():
    sources = ["Skills: python, k8s", "JD: PostgreSQL"]
    assert grounding_violations("Python, Kubernetes and PostgreSQL.", sources) == []
    assert grounding_violations("I led our Rust rewrite.", sources) == ["skill:rust"]
    # Same taxonomy group as python, but not implied by it.
    assert grounding_violations("Built Django apps.", sources) == ["skill:django"]
    assert grounding_violations("Cut latency by 40%.", sources) == ["number:40"]
    # Everyday words that double as skill names are not treated as claims.
    assert grounding_violations("I wish to express my interest.", sources) == []


# ------------------------------------------------------------- cover letter

def test_cover_letter_uses_grounded_llm_output(profile, store, bus, posting):
    calls: list = []
    client = _only("cover letter", GROUNDED_LETTER, calls)
    cover = _cover(store, bus, profile, posting, client)
    assert cover.strip() == GROUNDED_LETTER.strip()
    [(system, user)] = calls
    assert "never invent" in system.lower() and "indian job" in system.lower()
    assert "Python services on Kubernetes" in user and "PostgreSQL" in user
    assert "test@example.com" not in user


@pytest.mark.parametrize("response", ["", "   ", LLMError("timeout")])
def test_cover_letter_falls_back_on_empty_or_error(profile, store, bus, posting, response):
    cover = _cover(store, bus, profile, posting, _only("cover letter", response))
    assert cover.startswith("Dear Acme team,")
    assert "I'd welcome a chance to discuss" in cover


@pytest.mark.parametrize("bad", [
    GROUNDED_LETTER.replace("along with Redis", "along with Terraform"),
    GROUNDED_LETTER.replace("every day,", "every day, and cut latency by 40%,"),
    "Dear Hiring Team at Acme, I am a great fit. Regards, Test User",  # too short
])
def test_cover_letter_guard_falls_back(profile, store, bus, posting, bad):
    cover = _cover(store, bus, profile, posting, _only("cover letter", bad))
    assert cover.startswith("Dear Acme team,")
    assert "Terraform" not in cover and "40%" not in cover


# ------------------------------------------------------------ interview prep

_JOB = {"job_id": "j1", "title": "Backend Engineer", "company": "Acme",
        "jd_text": "Python, Kubernetes and PostgreSQL in production."}

_TAGGED = """\
[technical] How would you tune PostgreSQL queries behind a busy Python API?
[technical] How do you roll out a risky change on Kubernetes safely?
[technical] What would you monitor first for a slow Python service?
[behavioral] Tell me about a time you disagreed with a design decision?
[behavioral] Describe a production incident you owned. What happened?
[behavioral] Tell me about a time you had to learn something quickly?
[resume] In your Senior Backend Engineer role, how did you structure the services?
[resume] What was the hardest part of running Python services on Kubernetes?"""


def test_interview_questions_use_llm_with_existing_shape(profile):
    calls: list = []
    qs = generate_questions(profile, _JOB, None,
                            llm=resume_callback(_only("interview", _TAGGED, calls)))
    assert len(qs) == 8
    assert all(set(q) == {"type", "question"} for q in qs)
    assert [q["type"] for q in qs[:6]] == ["technical"] * 3 + ["behavioral"] * 3
    assert qs[0]["question"].startswith("How would you tune PostgreSQL")
    assert not any(q["question"].startswith("[") for q in qs)
    # Résumé questions are typed by content: both name a role skill.
    assert {q["type"] for q in qs[6:]} <= {"technical", "behavioral"}
    system, user = calls[0]
    assert "[resume]" in system and "never invent" in system.lower()
    assert "Python services on Kubernetes" in user
    assert "test@example.com" not in user


@pytest.mark.parametrize("response", [
    "", LLMError("boom"),
    _TAGGED.replace("tune PostgreSQL", "tune Cassandra"),  # skill in neither
])
def test_interview_questions_fall_back(profile, response):
    expected = generate_questions(profile, _JOB, None, llm=None)
    got = generate_questions(profile, _JOB, None,
                             llm=resume_callback(_only("interview", response)))
    assert got == expected


_RUBRIC = {"scores": {"structure": 0.8, "relevance": 0.9, "specificity": 1.4},
           "tips": ["State the result of the migration explicitly."],
           "overall": 0.85}


def test_interview_feedback_uses_rubric_llm_output():
    calls: list = []
    raw = "```json\n" + json.dumps(_RUBRIC) + "\n```"
    client = _only("interview coach", raw, calls)
    out = answer_feedback("Tell me about a migration.", "I migrated it.",
                          llm=resume_callback(client))
    assert out == {"scores": {"structure": 0.8, "relevance": 0.9,
                              "specificity": 1.0},  # clamped to 0..1
                   "tips": ["State the result of the migration explicitly."],
                   "overall": 0.85}
    system, user = calls[0]
    assert "STAR" in system and "I migrated it." in user


@pytest.mark.parametrize("response", [
    "", "not json", LLMError("timeout"),
    json.dumps({**_RUBRIC, "tips": []}),  # no actionable tips
])
def test_interview_feedback_falls_back_to_heuristic(response):
    q, a = "Tell me about a migration.", "I migrated it in phases and cut downtime."
    expected = answer_feedback(q, a, llm=None)
    got = answer_feedback(q, a, llm=resume_callback(_only("interview coach", response)))
    assert got == expected


# ----------------------------------------------------------------- outreach

_OUT_PROFILE = UserProfile(
    user_id="u", name="Ada Rao", email="ada@x.com", phone="+91 98765 43210",
    target_roles=[], locations=[], skills=["python", "kubernetes"],
    experiences=[{"title": "Backend Engineer", "company": "Zeta",
                  "bullets": ["Ran Python services on Kubernetes"]}],
)
_OUT_JOB = {"company": "Acme", "title": "Backend Engineer"}
_OUT_DOC = {"matched_keywords": ["python", "kubernetes"]}
_CONTACT = Contact(name="Jane Doe", email="jane@acme.com", title="Recruiter")

_NOTE = ("Hi Jane,\n\nI recently applied for the Backend Engineer role at Acme. "
         "At Zeta I ran Python services on Kubernetes, which matches what your "
         "team is hiring for. Would you be open to a short chat about the role?"
         "\n\nThanks,\nAda Rao")


def _draft(client=None):
    llm = resume_callback(client) if client is not None else None
    return draft_outreach(_OUT_PROFILE, _OUT_JOB, _OUT_DOC, _CONTACT, llm=llm)


def test_outreach_uses_grounded_llm_note():
    calls: list = []
    d = _draft(_only("outreach", _NOTE, calls))
    baseline = _draft()
    assert d["body"] == _NOTE
    assert set(d) == set(baseline) and d["subject"] == baseline["subject"]
    assert d["to"] == "jane@acme.com"
    system, user = calls[0]
    assert "never invent" in system.lower() and "Zeta" in user
    assert "ada@x.com" not in user and "98765" not in user


@pytest.mark.parametrize("response", [
    "", LLMError("timeout"),
    _NOTE.replace("Kubernetes,", "Kubernetes and Terraform,"),   # invented skill
    _NOTE.replace("At Zeta I", "At Zeta I cut costs by 30% and"),  # invented metric
    _NOTE + " Happy to share more." * 30,                       # over 120 words
])
def test_outreach_falls_back(response):
    assert _draft(_only("outreach", response)) == _draft()


# ---------------------------------------------------------------- endpoints

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def test_endpoints_use_env_llm_client(monkeypatch):
    import jobhunt.llm.factory as factory

    def responder(system: str, user: str) -> str:
        s = system.lower()
        if "outreach" in s:
            return _NOTE
        if "interview questions" in s or "[resume]" in s:
            return _TAGGED
        return ""

    monkeypatch.setattr(factory, "build_llm_client_from_env",
                        lambda: FakeLLMClient(responder))
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    state.user_profile = _OUT_PROFILE
    state.jobs = [{"job_id": "j1", "company": "Acme", "title": "Backend Engineer",
                   "url": "https://acme.com/j1", "status": "Saved", "events": []}]
    state.documents["j1"] = {"company": "Acme", "title": "Backend Engineer",
                             "matched_keywords": ["python", "kubernetes"],
                             "missing_keywords": ["postgresql"]}
    client = TestClient(create_app(state))

    r = client.post("/api/outreach/draft", json={
        "job_id": "j1", "contact_name": "Jane Doe", "contact_email": "jane@acme.com"})
    assert r.status_code == 200 and r.json()["body"] == _NOTE

    r = client.post("/api/interview/questions", json={"job_id": "j1"})
    assert r.status_code == 200
    qs = r.json()["questions"]
    assert len(qs) == 8 and qs[0]["question"].startswith("How would you tune PostgreSQL")
