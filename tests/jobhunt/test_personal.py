"""Personal mode: one owner, every capability on, profile from their résumé."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from jobhunt import personal  # noqa: E402
from jobhunt.approval import ApprovalState  # noqa: E402
from jobhunt.dashboard.server import DashboardState, create_app  # noqa: E402
from jobhunt.trace import ThoughtBus, TraceStore  # noqa: E402

RESUME = """ALEX TESTER
alex.tester@example.com | +91 98765 43210 | linkedin.com/in/alextester
Experience
Acme Data
— Junior Data Engineer
November 2025 – Present | Hyderabad, India
- Built PySpark pipelines on Databricks.
Technical Skills
Languages: Python, SQL, Golang
Data Engineering: PySpark, Databricks, ETL Pipelines
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME)
    monkeypatch.setenv("JOBHUNT_ME_RESUME", str(resume))
    monkeypatch.setenv("JOBHUNT_ME_ROLES", "Data Engineer")
    monkeypatch.setenv("JOBHUNT_ME_LOCATIONS", "Hyderabad, Bangalore, Remote")
    for key in ("JOBHUNT_ME_NAME", "JOBHUNT_ME_EMAIL", "JOBHUNT_ME_PHONE",
                "JOBHUNT_ME_AUTO_APPLY", "JOBHUNT_ME_DAILY_CAP",
                "JOBHUNT_ME_RELEVANCE_FLOOR"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _state() -> DashboardState:
    return DashboardState(trace_store=TraceStore(), bus=ThoughtBus())


def test_env_file_loads_without_overriding_the_shell(monkeypatch, tmp_path):
    f = tmp_path / "me.env"
    f.write_text('# comment\nexport A_KEY="quoted"\nB_KEY=plain\nC_KEY=from-file\n\n')
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.delenv("B_KEY", raising=False)
    monkeypatch.setenv("C_KEY", "from-shell")
    assert sorted(personal.load_env_file(f)) == ["A_KEY", "B_KEY"]
    import os
    assert os.environ["A_KEY"] == "quoted" and os.environ["B_KEY"] == "plain"
    assert os.environ["C_KEY"] == "from-shell"


def test_contact_is_read_from_the_resume_header():
    c = personal.contact_from_text(RESUME)
    assert c == {"name": "Alex Tester", "email": "alex.tester@example.com",
                 "phone": "+91 98765 43210"}


def test_profile_is_seeded_from_the_resume_file(env):
    state = _state()
    assert personal.seed_profile(state)
    p = state.user_profile
    assert p.name == "Alex Tester" and p.email == "alex.tester@example.com"
    assert p.target_roles == ["Data Engineer"]
    assert p.locations == ["Hyderabad", "Bangalore", "Remote"]
    assert {"pyspark", "databricks"} <= set(p.skills)
    # Co-pilot by default, with autonomy configured and one switch away.
    assert p.auto_apply is False
    assert (p.relevance_floor, p.daily_apply_cap) == (0.75, 5)
    assert p.application_answers["years_experience"] == str(p.experience_years)


def test_seeding_never_overwrites_an_existing_profile(env):
    state = _state()
    personal.seed_profile(state)
    state.user_profile.target_roles = ["Analytics Engineer"]
    assert personal.seed_profile(state) is None
    assert state.user_profile.target_roles == ["Analytics Engineer"]


def test_reseed_rebuilds_but_keeps_screening_answers(env):
    state = _state()
    personal.seed_profile(state)
    state.user_profile.application_answers["notice_period"] = "30 days"
    personal.seed_profile(state, force=True)
    assert state.user_profile.application_answers["notice_period"] == "30 days"


def _personal_client(state, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("<title>landing</title>")
    monkeypatch.setenv("JOBHUNT_FRONTEND_DIR", str(out))
    return TestClient(create_app(state, personal=True))


def test_personal_mode_skips_the_landing_page(env, monkeypatch, tmp_path):
    client = _personal_client(_state(), monkeypatch, tmp_path)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/dashboard/"


def test_status_reports_personal_mode_and_capabilities(env, monkeypatch, tmp_path):
    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    body = _personal_client(_state(), monkeypatch, tmp_path).get("/api/status").json()
    assert body["personal"] is True
    caps = {c["key"]: c for c in body["capabilities"]}
    assert caps["adzuna"]["on"] and caps["adzuna"]["hint"] == ""
    assert not caps["notifications"]["on"] and "TELEGRAM" in caps["notifications"]["hint"]


# --------------------------------------------------------------------------- #
# Approving routes through the real application form
# --------------------------------------------------------------------------- #

def _approvable(state):
    personal.seed_profile(state)
    job = {"job_id": "j1", "title": "Data Engineer", "company": "Acme",
           "location": "Hyderabad", "status": "Saved", "relevance_score": 0.9,
           "url": "https://job-boards.greenhouse.io/acme/jobs/1"}
    state.jobs = [job]
    state.documents["j1"] = {"job_id": "j1", "resume_text": "Alex\nPython",
                             "cover_letter_text": "Hello"}
    state.approval_queue.submit(job_id="j1", document_id="d1", company="Acme",
                                title="Data Engineer")
    return job


def test_copilot_opens_the_form_and_marks_applied_only_once_submitted(
        env, monkeypatch, tmp_path):
    from jobhunt.dashboard import server

    calls = {}

    def fake_browser_apply(plan, *, submit, headless, keep_open, on_finish=None, **_):
        calls.update(plan=plan, submit=submit, keep_open=keep_open, on_finish=on_finish)
        return {"ok": True, "mode": "copilot", "filled": ["Name"],
                "unfilled_required": ["Notice period"], "submitted": False, "detail": ""}

    monkeypatch.setattr(server, "_browser_route", lambda url, autonomous: True)
    monkeypatch.setattr("jobhunt.autofill.browser_apply", fake_browser_apply,
                        raising=False)
    state = _state()
    job = _approvable(state)
    client = _personal_client(state, monkeypatch, tmp_path)

    sub = client.post("/api/approve/j1?decision=approve").json()["submission"]
    assert sub["copilot"] is True and "Notice period" in sub["detail"]
    assert calls["submit"] is False and calls["keep_open"] is True
    assert calls["plan"]["applicant"]["email"] == "alex.tester@example.com"
    # Nothing has been sent yet.
    assert job["status"] == "Saved" and not job.get("submitted")

    calls["on_finish"]({"submitted": True})
    assert job["status"] == "Applied" and job["submitted"] is True
    assert state.approval_queue.by_job("j1")[0].state == ApprovalState.SUBMITTED


def test_closing_the_browser_without_submitting_is_not_an_application(
        env, monkeypatch, tmp_path):
    from jobhunt.dashboard import server

    finish = {}

    def fake_browser_apply(plan, *, on_finish=None, **_):
        finish["cb"] = on_finish
        return {"ok": True, "unfilled_required": [], "submitted": False}

    monkeypatch.setattr(server, "_browser_route", lambda url, autonomous: True)
    monkeypatch.setattr("jobhunt.autofill.browser_apply", fake_browser_apply,
                        raising=False)
    state = _state()
    job = _approvable(state)
    _personal_client(state, monkeypatch, tmp_path).post("/api/approve/j1?decision=approve")
    finish["cb"]({"submitted": False})
    assert job["status"] == "Saved" and not job.get("submitted")


def test_offline_mode_never_routes_to_a_browser(monkeypatch):
    from jobhunt.dashboard.server import _browser_route
    monkeypatch.setenv("JOBHUNT_AUTOFILL_ENABLED", "1")  # conftest pins OFFLINE=1
    assert _browser_route("https://job-boards.greenhouse.io/acme/jobs/1",
                          autonomous=True) is False


# --------------------------------------------------------------------------- #
# Laptop + phone on one database
# --------------------------------------------------------------------------- #

def _stored_state(db_path) -> DashboardState:
    from jobhunt.dashboard.persistence import DashboardStore
    state = DashboardState(trace_store=TraceStore(), bus=ThoughtBus(),
                           store=DashboardStore(str(db_path)))
    state.restore()
    return state


def test_two_processes_on_one_database_see_each_others_writes(env, tmp_path):
    laptop, phone = _stored_state(tmp_path / "s.db"), _stored_state(tmp_path / "s.db")
    personal.seed_profile(laptop)
    assert phone.refresh() is True and phone.user_profile.name == "Alex Tester"

    phone.user_profile.target_roles = ["Analytics Engineer"]
    phone.persist()
    assert laptop.refresh() is True
    assert laptop.user_profile.target_roles == ["Analytics Engineer"]
    # Nothing new since: no reload, so in-flight work is never swapped out.
    assert laptop.refresh() is False


def test_api_requests_pick_up_the_other_process_first(env, monkeypatch, tmp_path):
    laptop, phone = _stored_state(tmp_path / "s.db"), _stored_state(tmp_path / "s.db")
    client = _personal_client(phone, monkeypatch, tmp_path)
    personal.seed_profile(laptop)
    assert client.get("/api/status").json()["has_profile"] is True


def test_cron_sweep_needs_its_secret_and_bypasses_only_the_access_code(
        env, monkeypatch, tmp_path):
    from jobhunt.dashboard import server

    monkeypatch.setenv("CRON_SECRET", "s3cret")
    ran = []
    monkeypatch.setattr(server, "_discover_once",
                        lambda state, registry: ran.append(1) or {"added": 0})
    state = _state()
    personal.seed_profile(state)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("JOBHUNT_FRONTEND_DIR", str(out))
    client = TestClient(create_app(state, personal=True, access_code="pin"))

    assert client.get("/api/cron/sweep").status_code == 401
    assert client.get("/api/cron/sweep",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/cron/sweep", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and ran == [1]
    # The access code still guards everything else.
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"X-Access-Code": "pin"}).status_code == 200
