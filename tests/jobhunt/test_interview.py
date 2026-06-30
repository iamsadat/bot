"""Tests for AI interview prep: question generation + answer feedback."""

from __future__ import annotations

import pytest

from jobhunt.interview import answer_feedback, generate_questions


# --------------------------------------------------------------- generate_questions

def test_generate_questions_no_llm_returns_mixed_list(profile):
    job = {"job_id": "j1", "title": "Backend Engineer", "company": "Acme",
           "jd_text": "We need strong Python and Kubernetes experience with PostgreSQL."}
    questions = generate_questions(profile, job, doc=None, llm=None)

    assert questions
    types = {q["type"] for q in questions}
    assert "behavioral" in types
    assert "technical" in types
    for q in questions:
        assert q["type"] in ("behavioral", "technical")
        assert isinstance(q["question"], str) and q["question"].strip()


def test_technical_questions_reference_jd_keywords(profile):
    job = {"job_id": "j2", "title": "Platform Engineer", "company": "Acme",
           "jd_text": "Kubernetes Kubernetes Kubernetes Terraform Terraform Docker."}
    questions = generate_questions(profile, job, doc=None, llm=None)
    technical = [q["question"].lower() for q in questions if q["type"] == "technical"]
    assert technical
    assert any("kubernetes" in q for q in technical)


def test_technical_questions_fall_back_to_doc_keywords_without_jd_text(profile):
    job = {"job_id": "j3", "title": "Data Engineer", "company": "Acme"}
    doc = {"matched_keywords": ["sql", "airflow"], "missing_keywords": ["spark"]}
    questions = generate_questions(profile, job, doc=doc, llm=None)
    technical = [q["question"].lower() for q in questions if q["type"] == "technical"]
    assert technical
    assert any(("sql" in q) or ("airflow" in q) or ("spark" in q) for q in technical)


def test_generate_questions_with_no_keywords_still_returns_technical(profile):
    job = {"job_id": "j4", "title": "Engineer", "company": "Acme", "jd_text": ""}
    questions = generate_questions(profile, job, doc=None, llm=None)
    technical = [q for q in questions if q["type"] == "technical"]
    assert technical  # default technical question kicks in


def test_generate_questions_uses_llm_when_provided(profile):
    job = {"job_id": "j5", "title": "Backend Engineer", "company": "Acme",
           "jd_text": "Python and Kubernetes."}

    def fake_llm(action, payload):
        assert action == "interview_questions"
        return (
            "Tell me about a time you led a project under pressure.\n"
            "How would you use kubernetes to scale a flaky service?\n"
        )

    questions = generate_questions(profile, job, doc=None, llm=fake_llm)
    assert len(questions) == 2
    joined = " ".join(q["question"] for q in questions).lower()
    assert "kubernetes" in joined
    # The kubernetes-mentioning line should be classified technical.
    kube_q = next(q for q in questions if "kubernetes" in q["question"].lower())
    assert kube_q["type"] == "technical"


def test_generate_questions_falls_back_when_llm_raises(profile):
    job = {"job_id": "j6", "title": "Backend Engineer", "company": "Acme",
           "jd_text": "Python and Kubernetes and PostgreSQL."}

    def raising_llm(action, payload):
        raise RuntimeError("network down")

    questions = generate_questions(profile, job, doc=None, llm=raising_llm)
    assert questions
    types = {q["type"] for q in questions}
    assert "behavioral" in types
    assert "technical" in types


def test_generate_questions_falls_back_when_llm_returns_empty(profile):
    job = {"job_id": "j7", "title": "Backend Engineer", "company": "Acme",
           "jd_text": "Python and Kubernetes."}
    questions = generate_questions(profile, job, doc=None, llm=lambda a, p: "")
    assert questions
    assert any(q["type"] == "behavioral" for q in questions)
    assert any(q["type"] == "technical" for q in questions)


# ----------------------------------------------------------------- answer_feedback

def test_answer_feedback_no_llm_returns_score_keys():
    question = "Tell me about a time you used Python to ship a feature under a deadline."
    answer = (
        "In my last role, the situation was a tight launch deadline. My task was to "
        "ship a Python service. I built the API, wrote tests, and as a result we "
        "reduced latency by 30% and shipped on time for 10,000 users."
    )
    result = answer_feedback(question, answer, llm=None)
    assert set(result["scores"].keys()) == {"structure", "relevance", "specificity"}
    for v in result["scores"].values():
        assert 0.0 <= v <= 1.0
    assert 0.0 <= result["overall"] <= 1.0
    assert isinstance(result["tips"], list) and result["tips"]


def test_answer_feedback_short_vague_answer_scores_low():
    result = answer_feedback("Tell me about a challenge.", "It was hard.", llm=None)
    assert result["overall"] < 0.5
    assert result["tips"]


def test_answer_feedback_uses_llm_when_provided():
    def fake_llm(action, payload):
        assert action == "interview_feedback"
        return (
            '{"scores": {"structure": 0.9, "relevance": 0.8, "specificity": 0.7}, '
            '"tips": ["Great use of metrics."], "overall": 0.8}'
        )

    result = answer_feedback("Q?", "A.", llm=fake_llm)
    assert result["scores"]["structure"] == 0.9
    assert result["overall"] == 0.8
    assert result["tips"] == ["Great use of metrics."]


def test_answer_feedback_falls_back_when_llm_raises():
    def raising_llm(action, payload):
        raise RuntimeError("boom")

    result = answer_feedback("Q?", "Some reasonably detailed answer with numbers like 42%.",
                              llm=raising_llm)
    assert set(result["scores"].keys()) == {"structure", "relevance", "specificity"}
    assert 0.0 <= result["overall"] <= 1.0


def test_answer_feedback_falls_back_on_malformed_llm_json():
    result = answer_feedback("Q?", "An answer.", llm=lambda a, p: "not json")
    assert set(result["scores"].keys()) == {"structure", "relevance", "specificity"}


# --------------------------------------------------------------------------- API

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402


def _client():
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return state, TestClient(create_app(state))


def test_interview_questions_endpoint_404_for_unknown_job():
    _, client = _client()
    r = client.post("/api/interview/questions", json={"job_id": "nope"})
    assert r.status_code == 404


def test_interview_questions_endpoint_returns_questions():
    state, client = _client()
    state.jobs.append({
        "job_id": "j1", "title": "Backend Engineer", "company": "Acme",
        "url": "https://example.com/j1", "status": "Saved",
    })
    state.documents["j1"] = {
        "job_id": "j1", "company": "Acme", "title": "Backend Engineer",
        "matched_keywords": ["python"], "missing_keywords": ["kubernetes"],
    }
    r = client.post("/api/interview/questions", json={"job_id": "j1"})
    assert r.status_code == 200
    data = r.json()
    assert data["questions"]
    types = {q["type"] for q in data["questions"]}
    assert "behavioral" in types and "technical" in types


def test_interview_feedback_endpoint():
    _, client = _client()
    r = client.post("/api/interview/feedback", json={
        "question": "Tell me about a challenge you overcame.",
        "answer": "I once had to migrate a legacy system under a tight deadline; "
                   "I planned the work, executed it in phases, and reduced downtime by 40%.",
    })
    assert r.status_code == 200
    data = r.json()
    assert set(data["scores"].keys()) == {"structure", "relevance", "specificity"}
    assert 0.0 <= data["overall"] <= 1.0


def test_interview_feedback_endpoint_requires_fields():
    _, client = _client()
    r = client.post("/api/interview/feedback", json={"question": "", "answer": ""})
    assert r.status_code == 422
