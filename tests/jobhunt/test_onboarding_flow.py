"""The onboarding sequence a real user follows, end to end over the API.

Order matters here and is easy to get wrong: a user uploads a résumé *first* and
saves the form *second*. Anything the résumé measured has to survive that second
step.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.seniority import JUNIOR, level_from_profile  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402

RESUME = """
Sadat Khan
Experience
SAS2PY
— Junior Data Engineer
November 2025 – Present | Hyderabad, India
- Built PySpark pipelines on Databricks.
Technical Skills
Languages: Python, SQL, Golang
Data Engineering: PySpark, Databricks, ETL Pipelines
"""


def _client() -> tuple[DashboardState, TestClient]:
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus())
    return state, TestClient(create_app(state))


def test_resume_measurements_survive_saving_the_form():
    """Saving the profile used to build a fresh object and drop the measured
    experience, which left the seniority gate with no candidate level — so
    Staff roles came back."""
    state, client = _client()
    parsed = client.post("/api/onboarding/resume", json={"text": RESUME}).json()
    assert parsed["experience_years"] is not None

    # A user then fills in the form, which has no field for years.
    res = client.post("/api/onboarding/profile", json={
        "name": "Sadat", "email": "s@example.com",
        "target_roles": ["Data Engineer"], "locations": ["Hyderabad", "India"]})
    assert res.status_code == 200

    profile = state.user_profile
    assert profile is not None
    assert profile.experience_years == parsed["experience_years"]
    assert level_from_profile(profile) == JUNIOR


def test_parsed_sections_and_skills_survive_saving_the_form():
    state, client = _client()
    client.post("/api/onboarding/resume", json={"text": RESUME})
    client.post("/api/onboarding/profile", json={
        "name": "Sadat", "email": "s@example.com",
        "target_roles": ["Data Engineer"], "locations": ["Hyderabad"]})
    profile = state.user_profile
    assert "pyspark" in profile.skills
    assert profile.experiences, "parsed experience should not be discarded"


def test_form_values_still_win_over_the_resume():
    """Re-applying the parse must not override what the user typed."""
    state, client = _client()
    client.post("/api/onboarding/resume", json={"text": RESUME})
    client.post("/api/onboarding/profile", json={
        "name": "Sadat", "email": "s@example.com",
        "target_roles": ["Platform Engineer"], "locations": ["Remote"],
        "skills": ["rust"]})
    assert state.user_profile.target_roles == ["Platform Engineer"]
    assert state.user_profile.locations == ["Remote"]
    # Skills are the one field that unions rather than fills-empty: the résumé
    # is evidence of a skill, so typing one more does not retract the rest.
    assert "rust" in state.user_profile.skills
    assert "pyspark" in state.user_profile.skills


def test_autonomy_toggle_is_usable_without_the_user_connecting_a_board(monkeypatch):
    """The seeded company boards are real Greenhouse/Lever boards, so the
    "Connect an ATS to enable" dead end is gone. Auto-apply itself stays off."""
    monkeypatch.delenv("JOBHUNT_OFFLINE", raising=False)
    _, client = _client()
    autonomy = client.get("/api/autonomy").json()
    assert autonomy["ats_connected"] is True
    assert autonomy["auto_apply"] is False
    assert autonomy["daily_apply_cap"] == 0


def test_offline_mode_keeps_the_submission_gate_shut():
    """Fixtures use real-looking Greenhouse URLs, so offline must never submit."""
    _, client = _client()  # conftest pins JOBHUNT_OFFLINE=1
    assert client.get("/api/autonomy").json()["ats_connected"] is False


def test_sources_endpoint_discloses_which_boards_are_being_searched(monkeypatch):
    monkeypatch.delenv("JOBHUNT_OFFLINE", raising=False)
    _, client = _client()
    body = client.get("/api/sources").json()
    assert body["seeded_boards"] is True
    assert body["seeded_board_count"] > 0

    client.post("/api/onboarding/ats", json={"greenhouse_tokens": ["acme"]})
    assert client.get("/api/sources").json()["seeded_boards"] is False


TEMPLATE_RESUME = """P. SADATULLAH KHAN
DATA ENGINEER
Hyderabad, India  |  [Phone]  |  [Email]  |  [LinkedIn]
EXPERIENCE
Junior Data Engineer  |  [Company Name]  |  Feb 2026 – Present
• Built PySpark pipelines on Databricks.
SELECTED PROJECT
Real-Time Crypto Platform  |  Kafka, PySpark, Docker
• Streamed market data through Kafka.
OPEN SOURCE & PROBLEM SOLVING
Contributed to 4 open-source organizations.
EDUCATION
Bachelor's Degree in Information Technology  |  [University Name]
"""


def test_template_placeholders_are_blanked_and_named():
    """A résumé still holding "[Company Name]" would put exactly that into
    every tailored résumé and application."""
    from jobhunt.onboarding import parse_resume_text
    r = parse_resume_text(TEMPLATE_RESUME)
    assert {"Phone", "Email", "Company Name", "University Name"} <= set(r["placeholders"])
    assert r["experiences"][0]["company"] == ""
    assert "[" not in r["education"][0]["school"]
    assert r["contact"]["name"] == "P. Sadatullah Khan"
    assert r["inferred_titles"][0] == "Data Engineer"


def test_project_stack_and_achievements_are_not_projects():
    from jobhunt.onboarding import parse_resume_text
    r = parse_resume_text(TEMPLATE_RESUME)
    assert [p["name"] for p in r["projects"]] == ["Real-Time Crypto Platform"]
    assert r["projects"][0]["skills"] == ["kafka", "pyspark", "docker"]
    assert r["achievements"] == ["Contributed to 4 open-source organizations."]


def test_an_unsaved_upload_is_restored_on_return():
    """Leaving onboarding without saving looped back to an empty form."""
    _, client = _client()
    client.post("/api/onboarding/resume", json={"text": TEMPLATE_RESUME})
    body = client.get("/api/profile").json()
    assert body["profile"] is None
    assert body["pending_parse"]["contact"]["name"] == "P. Sadatullah Khan"
