"""Offline tests for browser apply (no network, no browser).

Covers the pure logic behind ``jobhunt.autofill.browser_apply``: URL routing,
name splitting, question-label -> answer-key rules, option choice (EEO
decline, yes/no, numeric ranges), fill planning, page-state checks, the
never-raise contract, and that nothing submit-like is clicked when
``submit=False`` in the spec-driven layer.
"""

from __future__ import annotations

import sys

import pytest

from jobhunt.autofill import (
    FakePage,
    GenericAutofiller,
    autofill_application,
    browser_apply,
    execute_fields,
    map_profile_to_fields,
    resolve_application_url,
    supports_browser_apply,
)
from jobhunt.autofill.generic import GENERIC_FIELD_SPECS
from jobhunt.autofill.questions import (
    best_option_index,
    captcha_challenge,
    choose_option,
    classify_control,
    looks_submitted,
    match_answer_key,
    plan_fill,
    short_label,
    split_name,
)
from jobhunt.autofill.registry import AutofillRegistry
from jobhunt.autofill.workday import WORKDAY_FIELD_SPECS
from jobhunt.models import UserProfile

# ------------------------------------------------------------- URL routing


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.lever.co/meesho/7d9af9b5-c1c7-48ec-bbb5-9b25e49f6596",
     "https://jobs.lever.co/meesho/7d9af9b5-c1c7-48ec-bbb5-9b25e49f6596/apply"),
    ("https://jobs.lever.co/cred/aa98a938-af3a-45f5-851d-966b2b5bad33/apply",
     "https://jobs.lever.co/cred/aa98a938-af3a-45f5-851d-966b2b5bad33/apply"),
    ("https://jobs.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33?lever-source=x",
     "https://jobs.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33/apply?lever-source=x"),
    ("https://jobs.eu.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33",
     "https://jobs.eu.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33/apply"),
    ("https://boards.greenhouse.io/phonepe/jobs/4012345",
     "https://job-boards.greenhouse.io/embed/job_app?for=phonepe&token=4012345"),
    ("https://job-boards.greenhouse.io/postman/jobs/7654321?gh_src=abc",
     "https://job-boards.greenhouse.io/embed/job_app?for=postman&token=7654321"),
    ("https://job-boards.eu.greenhouse.io/groww/jobs/4970739101",
     "https://job-boards.eu.greenhouse.io/embed/job_app?for=groww&token=4970739101"),
    ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=8172508",
     "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8172508"),
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs/4012345",
     "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4012345"),
    # Company careers page carrying gh_jid: Greenhouse resolves the board.
    ("https://databricks.com/company/careers/open-positions/job?gh_jid=8805655002",
     "https://boards.greenhouse.io/embed/job_app?token=8805655002"),
    ("https://stripe.com/jobs/search?gh_jid=8172508#app",
     "https://boards.greenhouse.io/embed/job_app?token=8172508"),
])
def test_resolve_application_url(url, expected):
    assert resolve_application_url(url) == expected
    assert supports_browser_apply(url) is True


@pytest.mark.parametrize("url", [
    "", "not a url", "https://acme.myworkdayjobs.com/en-US/careers/job/123",
    "https://jobs.lever.co/meesho",            # board listing, no posting
    "https://boards.greenhouse.io/phonepe",    # board listing, no posting
    "https://example.com/careers?gh_jid=abc",  # not a numeric job id
    "https://jobs.ashbyhq.com/acme/123",
    "https://www.linkedin.com/jobs/view/123456",
])
def test_unsupported_urls(url):
    assert supports_browser_apply(url) is False
    assert resolve_application_url(url) is None


# ------------------------------------------------------------ name split


@pytest.mark.parametrize("full,expected", [
    ("Test Candidate", ("Test", "Candidate")),
    ("Jane Marie Doe", ("Jane", "Marie Doe")),
    ("  Ada   Lovelace ", ("Ada", "Lovelace")),
    ("Doe, Jane", ("Jane", "Doe")),
    ("Prince", ("Prince", "")),
    ("", ("", "")),
])
def test_split_name(full, expected):
    assert split_name(full) == expected


# ---------------------------------------------------- label -> answer key


@pytest.mark.parametrize("label,key", [
    ("Are you legally authorized to work in the country in which you are applying?*",
     "work_authorization"),
    ("Do you have the right to work in India?", "work_authorization"),
    ("Will you now or in the future require sponsorship for employment visa status?",
     "requires_sponsorship"),
    ("Will you require Stripe to sponsor you for a work permit now or in the future?",
     "requires_sponsorship"),
    ("Do you need a visa to work here?", "requires_sponsorship"),
    ("Total Years of experience", "years_experience"),
    ("How many years of professional experience do you have?", "years_experience"),
    ("Notice Period (In days)", "notice_period"),
    ("What is the notice period in your current employment?", "notice_period"),
    ("How soon can you join?", "notice_period"),
    ("Current CTC (in LPA)", "current_ctc"),
    ("Current Compensation (Yearly)", "current_ctc"),
    ("What is your current fixed salary?", "current_ctc"),
    ("Expected CTC", "expected_ctc"),
    ("Expected Compensation (Yearly)", "salary_expectation"),
    ("What are your salary expectations?", "salary_expectation"),
    ("Are you willing to relocate to Bangalore?", "willing_to_relocate"),
    ("LinkedIn Profile", "linkedin"),
    ("GitHub URL", "github"),
    ("Portfolio URL", "portfolio"),
    ("Personal website", "website"),
    ("How did you hear about this job?", "how_did_you_hear"),
    ("How did you get to know about this role?", "how_did_you_hear"),
    ("Gender", "gender"),
    ("Are you Hispanic/Latino?", "race"),
    ("Race / Ethnicity", "race"),
    ("Veteran Status", "veteran_status"),
    ("Disability Status", "disability_status"),
    ("Who is your current or previous employer?", "current_company"),
    ("What is your current or previous job title?", "current_title"),
    ("Please select the country where you currently reside.", "country_of_residence"),
])
def test_match_answer_key(label, key):
    assert match_answer_key(label) == key


@pytest.mark.parametrize("label", [
    "Do you possess business fluency in Japanese?",
    "Why do you want to work here?",
    "Years of experience with React",            # skill-specific: never total YoE
    "Do you have a relative working at Groww?",
    "",
])
def test_unknown_labels_have_no_key(label):
    assert match_answer_key(label) is None


def test_long_self_id_prompt_uses_the_actual_question():
    # Meesho's prompt mentions race/ethnicity/disability in a preamble; the
    # question asked is gender.
    label = ("As part of our commitment to creating a diverse workplace, we invite you to "
             "voluntarily disclose your gender. Meesho considers applicants without regard "
             "to gender, race, ethnicity, disability, or any other characteristic. Please "
             "select the option that best describes your gender:")
    assert match_answer_key(label) == "gender"
    assert short_label(label) == "Please select the option that best describes your gender:"


# ---------------------------------------------------------------- options


def test_choose_option_exact_and_polarity():
    assert choose_option(["Yes", "No"], "yes") == "Yes"
    assert choose_option(["Yes", "No"], True) == "Yes"
    assert choose_option(["Yes", "No"], False) == "No"
    assert choose_option(["No", "Yes - I currently work at X", "Yes - Previous Intern"],
                         "No") == "No"
    # Two different "yes" options: ambiguous, so leave it to the human.
    assert choose_option(["No", "Yes - I currently work at X", "Yes - Previous Intern"],
                         "Yes") is None


def test_choose_option_eeo_declines_when_unanswered():
    assert choose_option(["Male", "Female", "Decline To Self Identify"], None,
                         key="gender") == "Decline To Self Identify"
    assert choose_option(["I am a veteran", "I am not a protected veteran",
                          "I don’t wish to answer"], "", key="veteran_status") \
        == "I don’t wish to answer"
    assert choose_option(["Male", "Female", "Prefer not to disclose"], "",
                         key="gender") == "Prefer not to disclose"
    assert choose_option(["Yes, I have a disability", "No",
                          "I do not want to answer"], None,
                         key="disability_status") == "I do not want to answer"
    # A provided answer wins over declining.
    assert choose_option(["Male", "Female", "Decline To Self Identify"], "female",
                         key="gender") == "Female"


def test_choose_option_never_invents_for_non_eeo():
    assert choose_option(["Male", "Female"], None, key="gender") is None
    assert choose_option(["Option A", "Option B"], "", key="how_did_you_hear") is None
    assert choose_option(["LinkedIn", "Naukri", "Others"], "Company website") is None


def test_choose_option_numeric_ranges_and_notice():
    assert choose_option(["0-2 years", "3-5 years", "5+ years"], "4") == "3-5 years"
    assert choose_option(["Less than 1 year", "1-3 years", "More than 3 years"],
                         "7 years") == "More than 3 years"
    notice = ["Immediate", "15 days", "30 days", "60 days", "90 days"]
    assert choose_option(notice, "30 days") == "30 days"
    assert choose_option(notice, "Immediate joiner") == "Immediate"
    assert choose_option(["Immediate", "1 month", "2 months", "3 months"],
                         "60 days") == "2 months"


def test_best_option_index():
    assert best_option_index(["India +91", "Indonesia +62"], "India") == 0
    assert best_option_index(["Yes", "No"], "No") == 1
    assert best_option_index(["Bengaluru, Karnataka, India"], "Bengaluru",
                             role="location") == 0
    assert best_option_index(["No options"], "Bengaluru", role="location") is None
    assert best_option_index(["Yes", "No"], "Maybe") is None


# ------------------------------------------------------------ classification


@pytest.mark.parametrize("ctrl,role", [
    ({"type": "text", "id": "first_name", "label": "First Name"}, "first_name"),
    ({"type": "text", "id": "last_name", "label": "Last Name"}, "last_name"),
    ({"type": "text", "id": "preferred_name", "label": "Preferred First Name"},
     "preferred_name"),
    ({"type": "text", "name": "name", "label": "Full name"}, "full_name"),
    ({"type": "email", "name": "email", "label": "Email"}, "email"),
    ({"type": "text", "id": "email", "label": "Email"}, "email"),
    ({"type": "tel", "id": "phone", "label": "Phone"}, "phone"),
    ({"type": "combobox", "id": "country", "label": "Country", "group": "phone"},
     "phone_country"),
    ({"type": "combobox", "id": "candidate-location", "label": "Location (City)"},
     "location"),
    ({"type": "text", "name": "location", "label": "Current location"}, "location"),
    ({"type": "file", "id": "resume", "label": "Resume/CV"}, "resume"),
    ({"type": "file", "name": "resume", "id": "resume-upload-input", "label": "Resume/CV"},
     "resume"),
    ({"type": "file", "id": "cover_letter", "label": "Cover Letter"}, "cover_letter_file"),
    ({"type": "textarea", "name": "comments", "label": "Additional information"},
     "cover_letter_text"),
    ({"type": "text", "name": "org", "label": "Current company"}, "current_company"),
    ({"type": "text", "name": "urls[LinkedIn]", "label": "LinkedIn URL"}, "linkedin"),
    ({"type": "combobox", "id": "question_1", "label": "Are you legally authorized to work?"},
     "work_authorization"),
])
def test_classify_control(ctrl, role):
    assert classify_control(ctrl) == role


# ------------------------------------------------------------------ planning


def _plan(**over):
    plan = {
        "url": "https://jobs.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33",
        "applicant": {"name": "Test Candidate", "email": "test@example.com",
                      "phone": "+91 98765 43210", "location": "Bengaluru, Karnataka, India"},
        "resume_pdf": b"%PDF-1.4 dummy",
        "cover_letter_text": "Hello",
        "answers": {"work_authorization": "Yes", "requires_sponsorship": "No",
                    "notice_period": "30 days", "current_ctc": "10 LPA"},
        "links": {"linkedin": "https://linkedin.com/in/test"},
    }
    plan.update(over)
    return plan


def _ctrl(handle, type_, label, required=False, options=(), **extra):
    opts = [{"handle": f"{handle}.{i}", "label": o} for i, o in enumerate(options)]
    return {"handle": str(handle), "type": type_, "label": label, "required": required,
            "value": "", "options": opts, "id": extra.pop("id", ""),
            "name": extra.pop("name", ""), **extra}


def test_plan_fill_lever_like_form():
    controls = [
        _ctrl(1, "text", "Full name", True, name="name"),
        _ctrl(2, "email", "Email", True, name="email"),
        _ctrl(3, "text", "Phone", True, name="phone"),
        _ctrl(4, "text", "Current company", True, name="org"),
        _ctrl(5, "text", "LinkedIn URL", name="urls[LinkedIn]"),
        _ctrl(6, "text", "GitHub URL", name="urls[GitHub]"),
        _ctrl(7, "radio", "Are you authorized to work in India?", True, ["Yes", "No"],
              name="cards[x][field0]"),
        _ctrl(8, "text", "Notice Period (In days)", True, name="cards[x][field1]"),
        _ctrl(9, "text", "Current CTC", True, name="cards[x][field2]"),
        _ctrl(10, "text", "Expected CTC", True, name="cards[x][field3]"),
        _ctrl(11, "radio", "Please select your gender", True,
              ["Male", "Female", "Prefer not to disclose"], name="cards[x][field4]"),
        _ctrl(12, "file", "Resume/CV", True, name="resume", id="resume-upload-input"),
    ]
    steps, unfilled = plan_fill(controls, _plan())
    by_label = {s.label: s for s in steps}
    assert steps[0].action == "upload" and steps[0].value == "resume"  # résumé first
    assert by_label["Full name"].value == "Test Candidate"
    assert by_label["Email"].value == "test@example.com"
    assert by_label["LinkedIn URL"].value == "https://linkedin.com/in/test"
    assert by_label["Are you authorized to work in India?"].action == "check"
    assert by_label["Are you authorized to work in India?"].handle == "7.0"  # "Yes"
    assert by_label["Notice Period (In days)"].value == "30 days"
    assert by_label["Current CTC"].value == "10 LPA"
    assert by_label["Please select your gender"].value == "Prefer not to disclose"
    # No GitHub link supplied and optional: skipped silently, never invented.
    assert "GitHub URL" not in by_label and "GitHub URL" not in unfilled
    # Required but unknown: left for the human.
    assert unfilled == ["Current company", "Expected CTC"]


def test_plan_fill_greenhouse_like_form():
    controls = [
        _ctrl(1, "text", "First Name", True, id="first_name", autocomplete="given-name"),
        _ctrl(2, "text", "Last Name", True, id="last_name"),
        _ctrl(3, "combobox", "Country", True, id="country", group="phone"),
        _ctrl(4, "tel", "Phone", True, id="phone"),
        _ctrl(5, "combobox", "Location (City)", True, id="candidate-location"),
        _ctrl(6, "file", "Cover Letter", id="cover_letter"),
        _ctrl(7, "combobox", "Will you require visa sponsorship?", True, ["Yes", "No"],
              id="question_2"),
        _ctrl(8, "combobox", "Gender", False, ["Male", "Female", "Decline To Self Identify"],
              id="gender"),
        _ctrl(9, "combobox", "Are you Hispanic/Latino?", id="hispanic_ethnicity"),
        _ctrl(10, "combobox", "Do you speak fluent Japanese?", True, ["Yes", "No"],
              id="question_9"),
        _ctrl(11, "checkbox", "I agree to the privacy policy", True, ["I agree"],
              id="consent"),
    ]
    steps, unfilled = plan_fill(controls, _plan())
    by_label = {s.label: s for s in steps}
    assert by_label["First Name"].value == "Test"
    assert by_label["Last Name"].value == "Candidate"
    assert by_label["Country"].action == "combobox"
    assert by_label["Country"].value == "India"           # from the +91 prefix
    assert by_label["Location (City)"].value == "Bengaluru, Karnataka, India"
    assert by_label["Cover Letter"].action == "upload"
    assert by_label["Will you require visa sponsorship?"].value == "No"
    assert by_label["Gender"].value == "Decline To Self Identify"
    lazy = by_label["Are you Hispanic/Latino?"]
    assert lazy.choose_in_ui and lazy.role == "race"      # options only exist in the UI
    # Unknown yes/no question and a consent box: the human answers those.
    assert unfilled == ["Do you speak fluent Japanese?", "I agree to the privacy policy"]


def test_default_yes_no_only_applies_to_required_yes_no_questions():
    controls = [
        _ctrl(1, "radio", "Are you based out of Bangalore?", True, ["Yes", "No"]),
        _ctrl(2, "radio", "Have you worked here before?", False, ["Yes", "No"]),
        _ctrl(3, "select", "Which team?", True, ["Platform", "Payments"]),
        _ctrl(4, "radio", "Gender", True, ["Male", "Female"]),
        _ctrl(5, "combobox", "Do you provide your consent for us to conduct your background "
              "verification?", True, ["Yes", "No"]),
    ]
    plan = _plan(answers={"default_yes_no": "No"})
    steps, unfilled = plan_fill(controls, plan)
    assert [(s.label, s.value) for s in steps] == [("Are you based out of Bangalore?", "No")]
    # EEO without a decline option, an unrelated select and a consent question
    # are never answered by the default.
    assert unfilled == ["Which team?", "Gender", "Do you provide your consent for us to "
                        "conduct your background verification?"]


def test_missing_resume_is_left_for_the_human():
    controls = [_ctrl(1, "file", "Resume/CV", True, id="resume")]
    steps, unfilled = plan_fill(controls, _plan(resume_pdf=None))
    assert steps == [] and unfilled == ["Resume/CV"]


# --------------------------------------------------------------- page state


def test_looks_submitted():
    assert looks_submitted(
        "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=acme&token=1")
    assert looks_submitted("https://jobs.lever.co/acme/abc-123/thanks")
    assert looks_submitted("https://example.com/x", "Thank you for applying!",
                           form_present=False)
    assert not looks_submitted("https://jobs.lever.co/acme/abc-123/apply")
    # Confirmation-ish copy next to a still-present form doesn't count.
    assert not looks_submitted("https://example.com/x", "Thank you for applying!",
                               form_present=True)


def test_captcha_challenge_only_counts_visible_widgets():
    invisible = [{"src": "https://www.google.com/recaptcha/enterprise/anchor?k=x&size=invisible",
                  "width": 256, "height": 60, "visible": True},
                 {"src": "https://www.google.com/recaptcha/api2/bframe?k=x",
                  "width": 400, "height": 580, "visible": False},
                 {"src": "https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html"
                         "#frame=checkbox-invisible", "width": 0, "height": 0,
                  "visible": True}]
    assert captcha_challenge(invisible) is None
    assert captcha_challenge([{"src": "https://www.google.com/recaptcha/api2/anchor?k=x",
                               "width": 304, "height": 78, "visible": True}]) == "reCAPTCHA"
    assert captcha_challenge([{"src": "https://newassets.hcaptcha.com/captcha/v1/x/static/"
                                      "hcaptcha.html#frame=challenge&id=1",
                               "width": 400, "height": 600, "visible": True}]) == "hCaptcha"


# ------------------------------------------------------------ public contract

_KEYS = {"ok", "mode", "filled", "unfilled_required", "submitted", "detail"}


def test_browser_apply_rejects_unsupported_url_without_raising():
    res = browser_apply({"url": "https://example.com/jobs/1"}, submit=False,
                        headless=True, keep_open=False)
    assert _KEYS <= set(res)
    assert res["ok"] is False and res["submitted"] is False and res["mode"] == "copilot"
    assert "unsupported" in res["detail"]


def test_browser_apply_bad_plan_never_raises():
    res = browser_apply(None, submit=True, headless=True, keep_open=False)  # type: ignore[arg-type]
    assert res["ok"] is False and res["mode"] == "auto"


def test_browser_apply_without_playwright_returns_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    res = browser_apply(_plan(), submit=False, headless=True, keep_open=False, dry_run=True)
    assert res["ok"] is False and res["mode"] == "dry_run"
    assert "pip install playwright && playwright install chromium" in res["detail"]


# ------------------------------------- spec-driven layer never clicks submit


def _profile() -> UserProfile:
    return UserProfile(user_id="u", name="Test Candidate", email="test@example.com",
                       target_roles=["backend"], locations=["Bengaluru"])


def test_execute_fields_never_clicks_without_submit():
    specs = [
        {"selector": "#email", "kind": "text", "label": "Email", "key": "email"},
        {"selector": "#next", "kind": "click", "label": "Save and Continue", "key": "submit",
         "required": True},
        {"selector": "#submit", "kind": "click", "label": "Submit Application"},
    ]
    page = FakePage({"#email", "#next", "#submit"})
    fields, requires_user = map_profile_to_fields(_profile(), {}, specs)
    filled, skipped, success = execute_fields(page, fields, requires_user, submit=False)
    assert not any(a[0] == "click" for a in page.actions)
    assert [f.label for f in filled] == ["Email"]
    assert success is True  # an un-clicked action button isn't a failure

    page = FakePage({"#email", "#next", "#submit"})
    fields, requires_user = map_profile_to_fields(_profile(), {}, specs)
    execute_fields(page, fields, requires_user, submit=True)
    assert ("click", "#next", "") in page.actions


def test_execute_fields_does_not_click_while_user_input_is_pending():
    specs = [
        {"selector": "#q", "kind": "text", "label": "Why us?", "required": True},
        {"selector": "#submit", "kind": "click", "label": "Submit Application"},
    ]
    page = FakePage({"#q", "#submit"})
    fields, requires_user = map_profile_to_fields(_profile(), {}, specs)
    assert requires_user == ["Why us?"]
    execute_fields(page, fields, requires_user, submit=True)
    assert not any(a[0] == "click" for a in page.actions)


@pytest.mark.parametrize("specs,url", [
    (GENERIC_FIELD_SPECS, "https://careers.example.com/apply/1"),
    (WORKDAY_FIELD_SPECS, "https://acme.myworkdayjobs.com/job/1"),
])
def test_autofillers_and_driver_never_click_in_copilot_mode(specs, url):
    present = {s["selector"] for s in specs} | {"button[type=submit]", "#submit-app"}
    answers = {"resume": "/tmp/r.pdf", "work_authorization": "yes", "phone": "1"}

    page = FakePage(present)
    AutofillRegistry().fill(page, url, _profile(), answers)
    assert not any(a[0] == "click" for a in page.actions)

    page = FakePage(present)
    autofill_application(url, _profile(), answers, page=page, submit=False)
    assert not any(a[0] == "click" for a in page.actions)


def test_generic_autofiller_clicks_its_submit_only_when_allowed():
    present = {s["selector"] for s in GENERIC_FIELD_SPECS}
    page = FakePage(present)
    GenericAutofiller().fill(page, _profile(), {"resume": "/tmp/r.pdf"})
    assert ("click", "#submit_application", "") not in page.actions
