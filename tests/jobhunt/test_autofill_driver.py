"""Tests for the real-browser autofill driver.

Offline tests inject a FakePage (no browser). A Playwright-guarded test exercises
the real PlaywrightPage adapter end-to-end against a local HTML form.
"""

from __future__ import annotations

import pytest

from jobhunt.autofill import FakePage, autofill_application
from jobhunt.autofill.driver import _attempt_submit
from jobhunt.models import UserProfile


def _profile():
    return UserProfile(user_id="u", name="Ada Lovelace", email="ada@x.com",
                       phone="555-1212", target_roles=["backend"], locations=["Remote"])


def test_driver_fills_generic_form_via_fakepage():
    page = FakePage({"#first_name", "#last_name", "#email", "#phone"})
    res = autofill_application("https://acme.com/careers/1", _profile(), {}, page=page)
    assert res.ats == "generic"
    labels = {f.label for f in res.filled}
    assert "First Name" in labels and "Email" in labels
    # goto was driven by the driver + the autofiller.
    assert any(a[0] == "goto" for a in page.actions)


def test_driver_dispatches_workday_by_url():
    page = FakePage(set())  # no selectors present → everything skips, but routed
    res = autofill_application("https://acme.myworkdayjobs.com/job/1", _profile(), {}, page=page)
    assert res.ats == "workday"


def test_copilot_does_not_submit_by_default():
    page = FakePage({"#first_name", "#email", "button[type=submit]"})
    autofill_application("https://acme.com/x", _profile(), {}, page=page, submit=False)
    assert ("click", "button[type=submit]", "") not in page.actions


def test_submit_clicks_when_requested_and_no_blockers():
    page = FakePage({"#first_name", "#last_name", "#email", "#phone",
                     "button[type=submit]"})
    res = autofill_application("https://acme.com/x", _profile(), {}, page=page, submit=True)
    if not res.requires_user:
        assert ("click", "button[type=submit]", "") in page.actions
        assert "submitted" in res.notes


def test_attempt_submit_finds_button():
    page = FakePage({"#submit-app"})
    assert _attempt_submit(page) is True
    assert _attempt_submit(FakePage(set())) is False


# ----- real browser (skipped where Playwright/chromium absent, e.g. CI) -----

def test_playwright_page_fills_real_form(tmp_path):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    html = """<html><body><form>
      <input id="first_name"><input id="last_name">
      <input id="email"><input id="phone">
    </form></body></html>"""
    f = tmp_path / "form.html"
    f.write_text(html)

    import glob
    import os
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or next(
        iter(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")), None)
    try:
        pw = sync_playwright().start()
        kwargs = {"headless": True}
        if exe:
            kwargs["executable_path"] = exe
        browser = pw.chromium.launch(**kwargs)
    except Exception as exc:  # browser not installed / sandbox issue
        pytest.skip(f"chromium unavailable: {exc}")
    try:
        page = browser.new_page()
        from jobhunt.autofill.driver import PlaywrightPage
        pwp = PlaywrightPage(page)
        res = autofill_application(f"file://{f}", _profile(), {}, page=pwp)
        assert {"First Name", "Email"} <= {x.label for x in res.filled}
        # The real DOM actually received the values.
        assert page.input_value("#email") == "ada@x.com"
    finally:
        browser.close()
        pw.stop()
