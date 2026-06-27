"""Tests for the ATS autofill subsystem (jobhunt.autofill).

Everything runs fully offline: a ``FakePage`` stands in for a real
browser driver (e.g. Playwright), so no network access or real browser
is ever needed. Mirrors the testing philosophy of
``jobhunt.submitters`` (FakePoster) and ``jobhunt.http`` (FakeHTTPClient).
"""

from __future__ import annotations

import pytest

from jobhunt.autofill import (
    Autofiller,
    AutofillError,
    AutofillRegistry,
    AutofillResult,
    FakePage,
    FormField,
    GenericAutofiller,
    IcimsAutofiller,
    Page,
    WorkdayAutofiller,
    map_profile_to_fields,
)
from jobhunt.autofill.generic import GENERIC_FIELD_SPECS
from jobhunt.autofill.icims import ICIMS_FIELD_SPECS
from jobhunt.autofill.workday import WORKDAY_FIELD_SPECS
from jobhunt.models import UserProfile


# --------------------------------------------------------------------- helpers

def _selectors(specs: list[dict]) -> set[str]:
    return {spec["selector"] for spec in specs}


def _make_profile(**overrides) -> UserProfile:
    defaults = dict(
        user_id="u1",
        name="Jane Marie Doe",
        email="jane.doe@example.com",
        target_roles=["backend engineer"],
        locations=["San Francisco, CA"],
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


# ============================================================== FakePage

class TestFakePage:
    def test_query_true_for_present_selector(self):
        page = FakePage(present_selectors={"#email"})
        assert page.query("#email") is True

    def test_query_false_for_absent_selector(self):
        page = FakePage(present_selectors={"#email"})
        assert page.query("#missing") is False

    def test_goto_records_action_and_sets_url(self):
        page = FakePage()
        page.goto("https://example.com/apply")
        assert page.url == "https://example.com/apply"
        assert page.actions == [("goto", "https://example.com/apply", "")]

    def test_fill_records_action(self):
        page = FakePage(present_selectors={"#first_name"})
        page.fill("#first_name", "Jane")
        assert page.actions == [("fill", "#first_name", "Jane")]

    def test_select_option_records_action(self):
        page = FakePage(present_selectors={"#country"})
        page.select_option("#country", "US")
        assert page.actions == [("select_option", "#country", "US")]

    def test_check_records_action(self):
        page = FakePage(present_selectors={"#agree"})
        page.check("#agree")
        assert page.actions == [("check", "#agree", "")]

    def test_click_records_action(self):
        page = FakePage(present_selectors={"#submit"})
        page.click("#submit")
        assert page.actions == [("click", "#submit", "")]

    def test_set_input_files_records_action(self):
        page = FakePage(present_selectors={"#resume"})
        page.set_input_files("#resume", "/tmp/resume.pdf")
        assert page.actions == [("set_input_files", "#resume", "/tmp/resume.pdf")]

    def test_text_returns_configured_value(self):
        page = FakePage(present_selectors={"#title"}, texts={"#title": "Apply Now"})
        assert page.text("#title") == "Apply Now"

    def test_text_returns_empty_string_when_not_configured(self):
        page = FakePage(present_selectors={"#title"})
        assert page.text("#title") == ""

    def test_exact_action_sequence_is_recorded_in_order(self):
        page = FakePage(present_selectors={"#a", "#b", "#c"})
        page.goto("https://x.test")
        page.fill("#a", "1")
        page.select_option("#b", "2")
        page.click("#c")
        assert page.actions == [
            ("goto", "https://x.test", ""),
            ("fill", "#a", "1"),
            ("select_option", "#b", "2"),
            ("click", "#c", ""),
        ]

    def test_acting_on_absent_selector_raises_autofill_error(self):
        cases = [
            ("fill", ("#missing", "x")),
            ("select_option", ("#missing", "x")),
            ("check", ("#missing",)),
            ("click", ("#missing",)),
            ("set_input_files", ("#missing", "/tmp/x")),
            ("text", ("#missing",)),
        ]
        for method, args in cases:
            page = FakePage(present_selectors=set())
            with pytest.raises(AutofillError):
                getattr(page, method)(*args)

    def test_error_message_mentions_selector(self):
        page = FakePage(present_selectors=set())
        with pytest.raises(AutofillError, match="#missing"):
            page.fill("#missing", "x")

    def test_query_never_raises_for_absent_selector(self):
        page = FakePage(present_selectors=set())
        assert page.query("#missing") is False  # query is the safe probe

    def test_implements_page_protocol(self):
        page = FakePage()
        assert isinstance(page, Page)


# ============================================================== mapper

class TestMapperProfileResolution:
    def test_resolves_first_and_last_name_split(self):
        profile = _make_profile(name="Jane Marie Doe")
        specs = [
            {"selector": "#fn", "kind": "text", "label": "First Name", "key": "first_name", "required": True},
            {"selector": "#ln", "kind": "text", "label": "Last Name", "key": "last_name", "required": True},
        ]
        fields, requires_user = map_profile_to_fields(profile, {}, specs)
        by_selector = {f.selector: f for f in fields}
        assert by_selector["#fn"].value == "Jane"
        assert by_selector["#ln"].value == "Marie Doe"
        assert requires_user == []

    def test_single_word_name_has_empty_last_name(self):
        profile = _make_profile(name="Madonna")
        specs = [
            {"selector": "#fn", "kind": "text", "label": "First Name", "key": "first_name", "required": True},
            {"selector": "#ln", "kind": "text", "label": "Last Name", "key": "last_name", "required": False},
        ]
        fields, requires_user = map_profile_to_fields(profile, {}, specs)
        by_selector = {f.selector: f for f in fields}
        assert by_selector["#fn"].value == "Madonna"
        assert by_selector["#ln"].value == ""
        # Unresolved optional last name with no key match falls to requires_user
        assert "Last Name" in requires_user

    def test_resolves_full_name_field(self):
        profile = _make_profile(name="Jane Marie Doe")
        specs = [{"selector": "#full", "kind": "text", "label": "Full Name", "key": "full_name", "required": True}]
        fields, _ = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == "Jane Marie Doe"

    def test_resolves_email_from_profile(self):
        profile = _make_profile(email="jane.doe@example.com")
        specs = [{"selector": "#email", "kind": "text", "label": "Email Address", "key": "email", "required": True}]
        fields, _ = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == "jane.doe@example.com"

    def test_resolves_email_via_label_only_no_key(self):
        profile = _make_profile(email="jane.doe@example.com")
        specs = [{"selector": "#email", "kind": "text", "label": "Your Email", "required": True}]
        fields, _ = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == "jane.doe@example.com"

    def test_resolves_location_from_profile_first_entry(self):
        profile = _make_profile(locations=["San Francisco, CA", "Remote"])
        specs = [{"selector": "#loc", "kind": "text", "label": "City", "key": "location", "required": False}]
        fields, _ = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == "San Francisco, CA"

    def test_case_insensitive_label_matching(self):
        profile = _make_profile(name="Jane Doe")
        specs = [{"selector": "#fn", "kind": "text", "label": "FIRST NAME", "required": True}]
        fields, _ = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == "Jane"


class TestMapperAnswersResolution:
    def test_fills_linkedin_from_answers(self):
        profile = _make_profile()
        specs = [{"selector": "#li", "kind": "text", "label": "LinkedIn Profile", "key": "linkedin", "required": False}]
        fields, requires_user = map_profile_to_fields(
            profile, {"linkedin": "https://linkedin.com/in/janedoe"}, specs
        )
        assert fields[0].value == "https://linkedin.com/in/janedoe"
        assert requires_user == []

    def test_fills_phone_from_answers(self):
        profile = _make_profile()
        specs = [{"selector": "#phone", "kind": "text", "label": "Phone Number", "key": "phone", "required": False}]
        fields, _ = map_profile_to_fields(profile, {"phone": "555-0100"}, specs)
        assert fields[0].value == "555-0100"

    def test_fills_arbitrary_answer_keyed_field(self):
        profile = _make_profile()
        specs = [{
            "selector": "#years",
            "kind": "text",
            "label": "Years of Experience",
            "key": "years_experience",
            "required": True,
        }]
        fields, requires_user = map_profile_to_fields(profile, {"years_experience": "8"}, specs)
        assert fields[0].value == "8"
        assert requires_user == []

    def test_answers_key_matching_is_normalized(self):
        """answers use snake_case keys; spec keys may use spaces or mixed case."""
        profile = _make_profile()
        specs = [{
            "selector": "#auth",
            "kind": "select",
            "label": "Are you authorized to work?",
            "key": "Work Authorization",
            "required": True,
        }]
        fields, requires_user = map_profile_to_fields(
            profile, {"work_authorization": "yes"}, specs
        )
        assert fields[0].value == "yes"
        assert requires_user == []

    def test_falls_back_to_normalized_label_when_no_key(self):
        profile = _make_profile()
        specs = [{"selector": "#q1", "kind": "text", "label": "Salary Expectation", "required": False}]
        fields, _ = map_profile_to_fields(profile, {"salary_expectation": "200000"}, specs)
        assert fields[0].value == "200000"

    def test_resume_file_field_resolved_from_answers(self):
        profile = _make_profile()
        specs = [{"selector": "#resume", "kind": "file", "label": "Resume", "key": "resume", "required": True}]
        fields, requires_user = map_profile_to_fields(
            profile, {"resume": "/tmp/jane_resume.pdf"}, specs
        )
        assert fields[0].value == "/tmp/jane_resume.pdf"
        assert fields[0].kind == "file"
        assert requires_user == []

    def test_resume_file_field_without_answer_requires_user(self):
        profile = _make_profile()
        specs = [{"selector": "#resume", "kind": "file", "label": "Resume", "key": "resume", "required": True}]
        fields, requires_user = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == ""
        assert "Resume" in requires_user


class TestMapperDefersUnknowns:
    def test_demographic_questions_always_deferred(self):
        labels = [
            "Veteran Status",
            "EEO Disclosure",
            "Disability Status",
            "Race/Ethnicity",
            "Gender Identity",
            "Sexual Orientation",
            "Self-Identification",
            "Equal Employment Opportunity",
        ]
        profile = _make_profile()
        for label in labels:
            specs = [{"selector": "#demo", "kind": "select", "label": label, "required": False}]
            # Even when an answer "exists" with a matching key, we must not use it.
            answers = {label.lower().replace(" ", "_").replace("/", "_"): "prefer-not-to-say"}
            fields, requires_user = map_profile_to_fields(profile, answers, specs)
            assert fields[0].value == ""
            assert label in requires_user

    def test_demographic_question_deferred_via_key_hint_even_if_label_is_generic(self):
        profile = _make_profile()
        specs = [{
            "selector": "#q",
            "kind": "select",
            "label": "Question 7",
            "key": "veteran_status",
            "required": False,
        }]
        fields, requires_user = map_profile_to_fields(profile, {"veteran_status": "no"}, specs)
        assert fields[0].value == ""
        assert "Question 7" in requires_user

    def test_unknown_essay_question_requires_user(self):
        profile = _make_profile()
        specs = [{
            "selector": "#essay",
            "kind": "text",
            "label": "Why do you want to work here?",
            "required": False,
        }]
        fields, requires_user = map_profile_to_fields(profile, {}, specs)
        assert fields[0].value == ""
        assert "Why do you want to work here?" in requires_user

    def test_unresolvable_field_never_gets_a_guessed_value(self):
        profile = _make_profile()
        specs = [{"selector": "#mystery", "kind": "text", "label": "Custom Question XYZ", "required": True}]
        fields, requires_user = map_profile_to_fields(profile, {"unrelated": "value"}, specs)
        assert fields[0].value == ""
        assert "Custom Question XYZ" in requires_user

    def test_click_action_fields_are_not_swept_into_requires_user(self):
        profile = _make_profile()
        specs = [{"selector": "#submit", "kind": "click", "label": "Submit Application", "required": True}]
        fields, requires_user = map_profile_to_fields(profile, {}, specs)
        assert requires_user == []
        assert fields[0].kind == "click"


# ============================================================== Workday

class TestWorkdayAutofiller:
    def _present_page(self) -> FakePage:
        return FakePage(present_selectors=_selectors(WORKDAY_FIELD_SPECS))

    def _answers(self) -> dict:
        return {
            "phone": "555-0100",
            "linkedin": "https://linkedin.com/in/janedoe",
            "work_authorization": "yes",
            "resume": "/tmp/resume.pdf",
        }

    def test_fills_required_fields_and_reports_success(self):
        page = self._present_page()
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert result.success is True
        assert result.ats == "workday"
        required_filled = [f for f in result.filled if f.required]
        assert {f.selector for f in required_filled} >= {
            "input[data-automation-id='legalNameSection_firstName']",
            "input[data-automation-id='legalNameSection_lastName']",
            "input[data-automation-id='email']",
            "input[data-automation-id='resumeUpload']",
            "select[data-automation-id='workAuthorization']",
        }

    def test_eeo_fields_go_to_requires_user_not_filled(self):
        page = self._present_page()
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert "Veteran Status (EEO)" in result.requires_user
        assert "Disability Status (EEO)" in result.requires_user
        filled_labels = {f.label for f in result.filled}
        assert "Veteran Status (EEO)" not in filled_labels
        assert "Disability Status (EEO)" not in filled_labels

    def test_goto_called_with_application_url(self):
        page = FakePage(present_selectors=_selectors(WORKDAY_FIELD_SPECS))
        page.url = "https://acme.myworkdayjobs.com/en-US/Careers/job/123"
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert page.actions[0] == (
            "goto", "https://acme.myworkdayjobs.com/en-US/Careers/job/123", ""
        )
        assert result.url == "https://acme.myworkdayjobs.com/en-US/Careers/job/123"

    def test_clicks_continue_button_last(self):
        page = self._present_page()
        profile = _make_profile()
        WorkdayAutofiller().fill(page, profile, self._answers())
        last_action = page.actions[-1]
        assert last_action[0] == "click"
        assert "bottom-navigation-next-button" in last_action[1]

    def test_missing_required_selector_skips_field_and_fails(self):
        # Omit the required first-name selector from the "DOM".
        missing = _selectors(WORKDAY_FIELD_SPECS) - {
            "input[data-automation-id='legalNameSection_firstName']"
        }
        page = FakePage(present_selectors=missing)
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert result.success is False
        skipped_selectors = {f.selector for f in result.skipped}
        assert "input[data-automation-id='legalNameSection_firstName']" in skipped_selectors

    def test_missing_optional_selector_does_not_fail_overall(self):
        missing = _selectors(WORKDAY_FIELD_SPECS) - {
            "input[data-automation-id='linkedinQuestion']"
        }
        page = FakePage(present_selectors=missing)
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert result.success is True
        skipped_selectors = {f.selector for f in result.skipped}
        assert "input[data-automation-id='linkedinQuestion']" in skipped_selectors

    def test_no_real_network_or_browser_only_fake_page(self):
        """Sanity: nothing in the autofiller reaches outside the FakePage."""
        page = self._present_page()
        profile = _make_profile()
        result = WorkdayAutofiller().fill(page, profile, self._answers())
        assert isinstance(result, AutofillResult)
        assert all(isinstance(a, tuple) and len(a) == 3 for a in page.actions)


# ============================================================== iCIMS

class TestIcimsAutofiller:
    def _present_page(self) -> FakePage:
        return FakePage(present_selectors=_selectors(ICIMS_FIELD_SPECS))

    def _answers(self) -> dict:
        return {
            "phone": "555-0199",
            "linkedin": "https://linkedin.com/in/janedoe",
            "work_authorization": "yes",
            "resume": "/tmp/resume.pdf",
        }

    def test_fills_required_fields_and_reports_success(self):
        page = self._present_page()
        profile = _make_profile()
        result = IcimsAutofiller().fill(page, profile, self._answers())
        assert result.success is True
        assert result.ats == "icims"

    def test_eeo_race_field_deferred(self):
        page = self._present_page()
        profile = _make_profile()
        result = IcimsAutofiller().fill(page, profile, self._answers())
        assert "Race/Ethnicity (EEO)" in result.requires_user

    def test_missing_required_resume_selector_fails(self):
        missing = _selectors(ICIMS_FIELD_SPECS) - {"input[name='iCIMS_Resume']"}
        page = FakePage(present_selectors=missing)
        profile = _make_profile()
        result = IcimsAutofiller().fill(page, profile, self._answers())
        assert result.success is False
        assert any(f.selector == "input[name='iCIMS_Resume']" for f in result.skipped)

    def test_action_sequence_starts_with_goto(self):
        page = self._present_page()
        page.url = "https://acme.icims.com/jobs/123/apply"
        profile = _make_profile()
        IcimsAutofiller().fill(page, profile, self._answers())
        assert page.actions[0][0] == "goto"

    def test_name_field_value_matches_profile(self):
        page = self._present_page()
        profile = _make_profile(name="Jane Marie Doe")
        IcimsAutofiller().fill(page, profile, self._answers())
        fill_actions = {sel: val for (m, sel, val) in page.actions if m == "fill"}
        assert fill_actions["input[name='iCIMS_FirstName']"] == "Jane"
        assert fill_actions["input[name='iCIMS_LastName']"] == "Marie Doe"


# ============================================================== Generic

class TestGenericAutofiller:
    def _present_page(self) -> FakePage:
        return FakePage(present_selectors=_selectors(GENERIC_FIELD_SPECS))

    def _answers(self) -> dict:
        return {
            "phone": "555-0177",
            "linkedin": "https://linkedin.com/in/janedoe",
            "resume": "/tmp/resume.pdf",
            "how_did_you_hear": "Company website",
        }

    def test_fills_required_fields_and_reports_success(self):
        page = self._present_page()
        profile = _make_profile()
        result = GenericAutofiller().fill(page, profile, self._answers())
        assert result.success is True
        assert result.ats == "generic"

    def test_gender_identity_field_deferred(self):
        page = self._present_page()
        profile = _make_profile()
        result = GenericAutofiller().fill(page, profile, self._answers())
        assert "Gender Identity (Demographic)" in result.requires_user

    def test_missing_required_email_selector_fails(self):
        missing = _selectors(GENERIC_FIELD_SPECS) - {"#email"}
        page = FakePage(present_selectors=missing)
        profile = _make_profile()
        result = GenericAutofiller().fill(page, profile, self._answers())
        assert result.success is False
        assert any(f.selector == "#email" for f in result.skipped)

    def test_optional_how_did_you_hear_filled_from_answers(self):
        page = self._present_page()
        profile = _make_profile()
        result = GenericAutofiller().fill(page, profile, self._answers())
        filled = {f.selector: f.value for f in result.filled}
        assert filled["#how_did_you_hear"] == "Company website"


# ============================================================== Registry

class TestAutofillRegistry:
    def test_for_url_routes_workday(self):
        reg = AutofillRegistry()
        autofiller = reg.for_url("https://acme.myworkdayjobs.com/en-US/Careers/job/123")
        assert isinstance(autofiller, WorkdayAutofiller)

    def test_for_url_routes_icims(self):
        reg = AutofillRegistry()
        autofiller = reg.for_url("https://acme.icims.com/jobs/123/apply")
        assert isinstance(autofiller, IcimsAutofiller)

    def test_for_url_routes_generic_for_unknown_domain(self):
        reg = AutofillRegistry()
        autofiller = reg.for_url("https://careers.example.com/apply/123")
        assert isinstance(autofiller, GenericAutofiller)

    def test_for_url_is_case_insensitive(self):
        reg = AutofillRegistry()
        autofiller = reg.for_url("https://ACME.MyWorkdayJobs.COM/job/1")
        assert isinstance(autofiller, WorkdayAutofiller)

    def test_fill_dispatches_to_workday_and_returns_result(self):
        reg = AutofillRegistry()
        page = FakePage(present_selectors=_selectors(WORKDAY_FIELD_SPECS))
        profile = _make_profile()
        answers = {"work_authorization": "yes", "resume": "/tmp/r.pdf"}
        result = reg.fill(page, "https://acme.myworkdayjobs.com/job/1", profile, answers)
        assert result.ats == "workday"

    def test_fill_dispatches_to_icims_and_returns_result(self):
        reg = AutofillRegistry()
        page = FakePage(present_selectors=_selectors(ICIMS_FIELD_SPECS))
        profile = _make_profile()
        answers = {"work_authorization": "yes", "resume": "/tmp/r.pdf"}
        result = reg.fill(page, "https://acme.icims.com/jobs/1", profile, answers)
        assert result.ats == "icims"

    def test_fill_dispatches_to_generic_for_unknown_domain(self):
        reg = AutofillRegistry()
        page = FakePage(present_selectors=_selectors(GENERIC_FIELD_SPECS))
        profile = _make_profile()
        answers = {"resume": "/tmp/r.pdf"}
        result = reg.fill(page, "https://careers.example.com/apply", profile, answers)
        assert result.ats == "generic"

    def test_register_adds_custom_route(self):
        class StubAutofiller:
            name = "stub"

            def fill(self, page, profile, answers) -> AutofillResult:
                return AutofillResult(ats="stub", url="", success=True)

        reg = AutofillRegistry()
        reg.register("customats.com", StubAutofiller())
        autofiller = reg.for_url("https://jobs.customats.com/apply/1")
        assert autofiller.name == "stub"

    def test_implements_autofiller_protocol(self):
        assert isinstance(WorkdayAutofiller(), Autofiller)
        assert isinstance(IcimsAutofiller(), Autofiller)
        assert isinstance(GenericAutofiller(), Autofiller)


# ============================================================== integration / using shared `profile` fixture

class TestUsingSharedProfileFixture:
    """Exercise the autofill stack against the repo-wide `profile` fixture
    from tests/jobhunt/conftest.py, to make sure it interoperates with the
    rest of the suite's UserProfile shape."""

    def test_workday_fill_against_shared_profile(self, profile):
        page = FakePage(present_selectors=_selectors(WORKDAY_FIELD_SPECS))
        answers = {"work_authorization": "yes", "resume": "/tmp/resume.pdf"}
        result = WorkdayAutofiller().fill(page, profile, answers)
        assert result.success is True
        filled = {f.selector: f.value for f in result.filled}
        assert filled["input[data-automation-id='legalNameSection_firstName']"] == "Test"
        assert filled["input[data-automation-id='legalNameSection_lastName']"] == "User"
        assert filled["input[data-automation-id='email']"] == "test@example.com"

    def test_registry_end_to_end_with_shared_profile(self, profile):
        reg = AutofillRegistry()
        page = FakePage(present_selectors=_selectors(GENERIC_FIELD_SPECS))
        answers = {"resume": "/tmp/resume.pdf"}
        result = reg.fill(page, "https://jobs.unknownats.io/apply/42", profile, answers)
        assert result.success is True
        assert result.ats == "generic"


# ============================================================== FormField / AutofillResult dataclasses

class TestDataclasses:
    def test_form_field_defaults(self):
        f = FormField(selector="#x", kind="text")
        assert f.value == ""
        assert f.label == ""
        assert f.required is False
        assert f.filled is False

    def test_autofill_result_defaults(self):
        r = AutofillResult(ats="generic", url="https://x.test")
        assert r.filled == []
        assert r.skipped == []
        assert r.requires_user == []
        assert r.success is False
        assert r.notes == ""
