"""Real-browser tests for browser_apply against local fixture pages.

Skipped unless Playwright and a Chromium build are available. Nothing touches
the network: the fixture page is served for the posting's hosted-form URL by
a context route and every other request is aborted.
"""

from __future__ import annotations

import threading
import time

import pytest

from jobhunt.autofill import apply as A

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module", autouse=True)
def _chromium_available():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            A._launch_browser(pw, headless=True).close()
    except Exception as exc:  # browser not installed / sandbox restrictions
        pytest.skip(f"chromium unavailable: {exc}")


LEVER_URL = "https://jobs.lever.co/acme/aa98a938-af3a-45f5-851d-966b2b5bad33"
GH_URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"

LEVER_HTML = """<!doctype html><html><body>
<form id="application-form" method="POST" action="/acme/aa98a938-af3a-45f5-851d-966b2b5bad33/apply">
<ul>
 <li class="application-question"><label><div class="application-label">Resume/CV
   <span class="required">&#10033;</span></div><div class="application-field">
   <input type="file" id="resume-upload-input" name="resume"></div></label></li>
 <li class="application-question"><label><div class="application-label">Full name
   <span class="required">&#10033;</span></div><div class="application-field">
   <input type="text" name="name" required></div></label></li>
 <li class="application-question"><label><div class="application-label">Email
   <span class="required">&#10033;</span></div><div class="application-field">
   <input type="email" name="email" required></div></label></li>
 <li class="application-question"><label><div class="application-label">Phone
   <span class="required">&#10033;</span></div><div class="application-field">
   <input type="text" name="phone" required></div></label></li>
 <li class="application-question"><label><div class="application-label">Current company
   <span class="required">&#10033;</span></div><div class="application-field">
   <input type="text" name="org" required></div></label></li>
 <li class="application-question"><label><div class="application-label">LinkedIn URL</div>
   <div class="application-field"><input type="text" name="urls[LinkedIn]"></div></label></li>
 <li class="application-question custom-question"><div><div class="application-label">
   <div class="text">Are you legally authorized to work in India?
   <span class="required">&#10033;</span></div></div><div class="application-field required-field">
   <ul><li><label><input type="radio" name="cards[c][field0]" value="Yes" required>
   <span class="application-answer-alternative">Yes</span></label></li>
   <li><label><input type="radio" name="cards[c][field0]" value="No" required>
   <span class="application-answer-alternative">No</span></label></li></ul></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label">
   <div class="text">Please select the option that best describes your gender:
   <span class="required">&#10033;</span></div></div><div class="application-field required-field">
   <ul><li><label><input type="radio" name="cards[c][field1]" value="Male" required>
   <span class="application-answer-alternative">Male</span></label></li>
   <li><label><input type="radio" name="cards[c][field1]" value="Prefer not to disclose" required>
   <span class="application-answer-alternative">Prefer not to disclose</span></label></li>
   </ul></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label">
   <div class="text">Notice Period (In days)<span class="required">&#10033;</span></div></div>
   <div class="application-field"><input type="text" name="cards[c][field2]" required
   class="card-field-input"></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label">
   <div class="text">How did you get to know about this role?<span class="required">&#10033;</span>
   </div></div><div class="application-field"><select name="cards[c][field3]" required>
   <option value="">Select...</option><option>Linkedin</option><option>Naukri</option>
   <option>Others</option></select></div></div></li>
</ul>
<button id="btn-submit" type="button" class="template-btn-submit">Submit application</button>
</form>
<script>
 document.getElementById('btn-submit').addEventListener('click',
   () => document.getElementById('application-form').submit());
 fetch('/beacon', {method: 'POST', body: 'x'}).catch(() => {});
</script></body></html>"""

# Minimal stand-in for Greenhouse's react-select comboboxes (same DOM contract:
# input[role=combobox]#id, options #react-select-<id>-option-N, .select__single-value).
GH_HTML = """<!doctype html><html><body>
<form id="application-form" method="get">
 <label for="first_name">First Name<span>*</span></label>
 <input id="first_name" type="text" aria-required="true" autocomplete="given-name">
 <label for="last_name">Last Name<span>*</span></label>
 <input id="last_name" type="text" aria-required="true" autocomplete="family-name">
 <label for="email">Email<span>*</span></label>
 <input id="email" type="text" aria-required="true">
 <label for="phone">Phone<span>*</span></label><input id="phone" type="tel" aria-required="true">
 <div role="group" aria-labelledby="upload-label-resume" aria-required="true">
  <div id="upload-label-resume">Resume/CV<span class="required">*</span></div>
  <input id="resume" type="file"></div>
 <div class="combo" data-id="question_1" data-label="Are you legally authorized to work here?"
      data-options="Yes|No"></div>
 <div class="combo" data-id="question_2" data-label="Do you speak fluent Klingon?"
      data-options="Yes|No"></div>
 <div class="combo" data-id="gender" data-label="Gender" data-optional="1"
      data-options="Male|Female|Decline To Self Identify"></div>
 CAPTCHA_SLOT
 <button type="submit">Submit application</button>
</form>
<script>
 for (const host of document.querySelectorAll('.combo')) {
   const id = host.dataset.id, opts = host.dataset.options.split('|');
   const star = host.dataset.optional ? '' : '<span aria-hidden="true">*</span>';
   host.innerHTML = `<label id="${id}-label" for="${id}">${host.dataset.label}${star}</label>
     <div class="select__control"><div class="select__value-container">
     <div class="select__input-container"><input class="select__input" id="${id}"
     role="combobox" aria-labelledby="${id}-label" aria-required="${!host.dataset.optional}">
     </div></div></div><div class="menu-host"></div>`;
   const input = host.querySelector('input'), menu = host.querySelector('.menu-host');
   const render = () => {
     const q = input.value.toLowerCase();
     menu.innerHTML = '<div class="select__menu" role="listbox">' + opts
       .filter(o => o.toLowerCase().includes(q))
       .map((o, i) => `<div role="option" id="react-select-${id}-option-${i}">${o}</div>`)
       .join('') + '</div>';
     for (const el of menu.querySelectorAll('[role=option]')) el.addEventListener('click', () => {
       host.querySelector('.select__value-container').insertAdjacentHTML('afterbegin',
         `<div class="select__single-value">${el.textContent}</div>`);
       input.value = ''; menu.innerHTML = '';
     });
   };
   input.addEventListener('click', render);
   input.addEventListener('input', render);
 }
 document.getElementById('application-form').addEventListener('submit', (e) => {
   e.preventDefault();
   history.pushState({}, '', '/embed/job_app/confirmation?for=acme&token=4012345');
   document.body.innerHTML = '<h1>Thank you for applying!</h1>';
 });
 if (window.__humanSubmitMs) setTimeout(
   () => document.getElementById('application-form').requestSubmit(), window.__humanSubmitMs);
</script></body></html>"""

# Classic boards.greenhouse.io markup: wrapping labels, native selects,
# an EEO select with a decline option and a button-type #submit_app.
CLASSIC_HTML = """<!doctype html><html><body>
<form id="application_form" method="post" action="/acme/jobs/4012345">
 <div class="field"><label for="first_name">First Name <span class="asterisk">*</span></label>
  <input type="text" id="first_name" name="job_application[first_name]" aria-required="true"></div>
 <div class="field"><label for="last_name">Last Name <span class="asterisk">*</span></label>
  <input type="text" id="last_name" name="job_application[last_name]" aria-required="true"></div>
 <div class="field"><label for="email">Email <span class="asterisk">*</span></label>
  <input type="text" id="email" name="job_application[email]" aria-required="true"></div>
 <div class="field"><label>LinkedIn Profile <span class="asterisk">*</span><br>
  <input type="text" id="job_application_answers_attributes_0_text_value"
   name="job_application[answers_attributes][0][text_value]" aria-required="true"></label></div>
 <div class="field"><label>Will you now or in the future require sponsorship?
  <span class="asterisk">*</span><br>
  <select id="job_application_answers_attributes_1_boolean_value"
   name="job_application[answers_attributes][1][boolean_value]" aria-required="true">
  <option value="">--</option><option value="1">Yes</option><option value="0">No</option>
  </select></label></div>
 <div class="field"><label for="job_application_gender">Gender</label>
  <select id="job_application_gender" name="job_application[gender]">
  <option value="">Please select</option><option value="1">Male</option>
  <option value="2">Female</option><option value="3">Decline To Self Identify</option>
  </select></div>
 <input type="button" id="submit_app" value="Submit Application">
</form>
<script>
 document.getElementById('submit_app').addEventListener('click',
   () => document.getElementById('application_form').submit());
</script></body></html>"""

RECAPTCHA = ('<iframe src="https://www.google.com/recaptcha/api2/anchor?k=x" '
             'width="304" height="78"></iframe>')


def _serve(monkeypatch, url: str, html: str, init_script: str = "") -> None:
    hosted = A.resolve_application_url(url)
    original = A._new_context

    def patched(browser, *, headless, dry_run):
        ctx = original(browser, headless=headless, dry_run=dry_run)
        if init_script:
            ctx.add_init_script(init_script)
        ctx.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body=html)
            if route.request.url == hosted else route.abort())
        return ctx

    monkeypatch.setattr(A, "_new_context", patched)


def _plan(url: str, **answers) -> dict:
    return {
        "url": url,
        "applicant": {"name": "Test Candidate", "email": "test@example.com",
                      "phone": "+91 98765 43210", "location": "Bengaluru, India"},
        "resume_pdf": b"%PDF-1.4\n%%EOF\n",
        "cover_letter_text": "Dry-run test.",
        "answers": {"work_authorization": "Yes", "notice_period": "30 days",
                    "how_did_you_hear": "LinkedIn", **answers},
        "links": {"linkedin": "https://linkedin.com/in/test-candidate"},
    }


def test_lever_dry_run_fills_and_blocks_writes(monkeypatch):
    _serve(monkeypatch, LEVER_URL, LEVER_HTML)
    res = A.browser_apply(_plan(LEVER_URL), submit=False, headless=True, keep_open=False,
                          dry_run=True)
    assert res["ok"] is True and res["mode"] == "dry_run" and res["submitted"] is False
    for label in ("Resume/CV", "Full name", "Email", "Phone", "LinkedIn URL",
                  "Are you legally authorized to work in India?",
                  "Please select the option that best describes your gender:",
                  "Notice Period (In days)", "How did you get to know about this role?"):
        assert label in res["filled"]
    assert res["unfilled_required"] == ["Current company"]
    # The page's background POST was aborted; the form itself was never posted.
    assert any(r.startswith("POST ") and r.endswith("/beacon") for r in res["blocked_requests"])
    assert not any("/apply" in r for r in res["blocked_requests"])


def test_classic_greenhouse_form_dry_run(monkeypatch):
    _serve(monkeypatch, GH_URL, CLASSIC_HTML)
    res = A.browser_apply(_plan(GH_URL, requires_sponsorship="No"), submit=False,
                          headless=True, keep_open=False, dry_run=True)
    assert res["ok"] is True, res
    assert set(res["filled"]) == {"First Name", "Last Name", "Email", "LinkedIn Profile",
                                  "Will you now or in the future require sponsorship?",
                                  "Gender"}
    assert res["unfilled_required"] == []
    assert res["blocked_requests"] == []  # the form POST was never attempted


def test_greenhouse_auto_mode_refuses_with_unanswered_required(monkeypatch):
    _serve(monkeypatch, GH_URL, GH_HTML.replace("CAPTCHA_SLOT", ""))
    res = A.browser_apply(_plan(GH_URL), submit=True, headless=True, keep_open=False)
    assert res["mode"] == "auto" and res["ok"] is False and res["submitted"] is False
    assert res["unfilled_required"] == ["Do you speak fluent Klingon?"]
    assert "Gender" in res["filled"]  # optional EEO -> "Decline To Self Identify"


def test_greenhouse_auto_mode_submits_and_detects_confirmation(monkeypatch):
    _serve(monkeypatch, GH_URL, GH_HTML.replace("CAPTCHA_SLOT", ""))
    res = A.browser_apply(_plan(GH_URL, default_yes_no="No"), submit=True, headless=True,
                          keep_open=False)
    assert res["ok"] is True and res["submitted"] is True, res
    assert "confirmation" in res["detail"]


def test_auto_mode_never_solves_a_captcha(monkeypatch):
    _serve(monkeypatch, GH_URL, GH_HTML.replace("CAPTCHA_SLOT", RECAPTCHA))
    res = A.browser_apply(_plan(GH_URL, default_yes_no="No"), submit=True, headless=True,
                          keep_open=False)
    assert res["ok"] is False and res["submitted"] is False
    assert "reCAPTCHA" in res["detail"]


def test_copilot_keep_open_reports_human_submission(monkeypatch):
    monkeypatch.setenv("JOBHUNT_COPILOT_AFTER_SUBMIT_SECONDS", "0")
    monkeypatch.setenv("JOBHUNT_COPILOT_MAX_MINUTES", "1")
    # The page plays the human: it submits itself a few seconds after load.
    _serve(monkeypatch, GH_URL, GH_HTML.replace("CAPTCHA_SLOT", ""),
           init_script="window.__humanSubmitMs = 6000;")
    finished = threading.Event()
    seen: dict = {}

    def on_finish(info: dict) -> None:
        seen.update(info)
        finished.set()

    res = A.browser_apply(_plan(GH_URL), submit=False, headless=True, keep_open=True,
                          on_finish=on_finish)
    returned_at = time.monotonic()
    assert res["mode"] == "copilot" and res["ok"] is True and res["submitted"] is False
    assert not finished.is_set()  # returned while the browser is still open
    assert finished.wait(60)
    assert seen["submitted"] is True
    assert time.monotonic() >= returned_at
