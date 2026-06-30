"""Real-browser autofill driver (Playwright) behind the Page protocol.

``PlaywrightPage`` adapts a Playwright sync page to the autofill ``Page``
protocol, so the existing Workday/iCIMS/generic autofillers drive a real
browser. ``autofill_application`` launches the browser (optional ``playwright``
dependency), navigates, runs the registry, and — only when ``submit=True`` —
clicks a submit button. Default is **co-pilot**: fill the form and leave the
final submit to the user.

The driver is offline-testable by injecting a ``FakePage``; the real browser
path is exercised by a Playwright-guarded test.
"""

from __future__ import annotations

import logging
from typing import Any

from jobhunt.autofill.base import AutofillResult, Page
from jobhunt.autofill.registry import AutofillRegistry

logger = logging.getLogger(__name__)

_SUBMIT_SELECTORS = (
    "button[type=submit]", "input[type=submit]",
    "[data-automation-id=bottom-navigation-next-button]", "#submit-app",
    "button:has-text('Submit')",
)


class PlaywrightPage:
    """Adapt a ``playwright.sync_api.Page`` to the autofill ``Page`` protocol."""

    def __init__(self, page: Any, *, action_timeout_ms: int = 4000) -> None:
        self._p = page
        try:
            page.set_default_timeout(action_timeout_ms)
        except Exception:
            logger.debug("could not set default timeout on page", exc_info=True)

    @property
    def url(self) -> str:
        return getattr(self._p, "url", "") or ""

    def goto(self, url: str) -> None:
        self._p.goto(url)

    def fill(self, selector: str, value: str) -> None:
        self._p.fill(selector, value)

    def select_option(self, selector: str, value: str) -> None:
        self._p.select_option(selector, value)

    def check(self, selector: str) -> None:
        self._p.check(selector)

    def click(self, selector: str) -> None:
        self._p.click(selector)

    def set_input_files(self, selector: str, path: str) -> None:
        self._p.set_input_files(selector, path)

    def query(self, selector: str) -> bool:
        try:
            return self._p.query_selector(selector) is not None
        except Exception:
            logger.debug("query_selector failed for %r", selector, exc_info=True)
            return False

    def text(self, selector: str) -> str:
        el = self._p.query_selector(selector)
        return el.inner_text() if el is not None else ""


def _launch_real_page(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "real-browser autofill needs Playwright — pip install playwright "
            "&& playwright install chromium"
        ) from exc
    import os
    pw = sync_playwright().start()
    # Honor an explicit chromium binary (e.g. a pre-provisioned browser) so we
    # don't depend on `playwright install` in constrained environments.
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    launch_kwargs: dict = {"headless": headless}
    if exe:
        launch_kwargs["executable_path"] = exe
    browser = pw.chromium.launch(**launch_kwargs)
    page = browser.new_page()

    def close() -> None:
        try:
            browser.close()
        finally:
            pw.stop()

    return PlaywrightPage(page), close


def _attempt_submit(page: Page) -> bool:
    for sel in _SUBMIT_SELECTORS:
        if page.query(sel):
            try:
                page.click(sel)
                return True
            except Exception:
                logger.debug("submit click failed for selector %r", sel, exc_info=True)
                continue
    return False


def autofill_application(
    url: str,
    profile: Any,
    answers: dict[str, str],
    *,
    page: Page | None = None,
    submit: bool = False,
    headless: bool = True,
    registry: AutofillRegistry | None = None,
    limiter: Any = None,
) -> AutofillResult:
    """Autofill the application form at *url* (co-pilot by default).

    Pass ``page`` (e.g. a FakePage) to drive without a real browser. With
    ``submit=False`` (default) the form is filled but never submitted — the
    user confirms. ``submit=True`` clicks a submit button only when no field
    still ``requires_user``.
    """
    if limiter is not None:
        limiter.acquire()
    registry = registry or AutofillRegistry()
    own_page = page is None
    closer = None
    if own_page:
        page, closer = _launch_real_page(headless)
    try:
        page.goto(url)
        result = registry.fill(page, url, profile, answers)
        if submit and not result.requires_user:
            if _attempt_submit(page):
                result.notes += " (submitted)"
            else:
                result.notes += " (no submit button found)"
        return result
    finally:
        if closer is not None:
            closer()
